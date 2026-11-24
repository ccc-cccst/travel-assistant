"""One runtime for request interpretation, planning, validation and selection."""

import json
import time

from chinatravel.agent.utils import NpEncoder
from .preferences import normalize_profile, evaluate_preferences


class PlanningService:
    def __init__(self, env=None):
        if env is None:
            from chinatravel.environment.world_env import WorldEnv
            env = WorldEnv(lang="zh")
        self.env = env

    def prepare(self, query, max_seconds=15):
        started = time.perf_counter()
        response = {"status": "invalid_request", "plan": None, "issues": [], "metrics": {}}
        stage = "input"
        llm_calls = 0
        try:
            if not isinstance(query, dict):
                raise ValueError("需求应为 JSON 对象")
            if isinstance(max_seconds, bool) or not isinstance(max_seconds, (float, int)) or not 0 < max_seconds <= 300:
                raise ValueError("规划时限应在 0 到 300 秒之间")
            from .requirements import confirm_appointments, parse_request
            from .model_requirements import configured_model, interpret
            explicit_profile = query.get("profile")
            normalized = normalize_profile(explicit_profile)
            request = parse_request({**query, "profile": normalized}, self.env)
            spec = request.to_dict()
            response["request"] = spec
            if spec.get("nature_language", "").strip():
                stage = "interpretation"
                model = configured_model()
                if model is not None:
                    llm_calls += 1
                    model_started = time.perf_counter()
                    response["metrics"]["model"] = model.model_name
                    try:
                        interpret(request, self.env, model, timeout=max(0.01, max_seconds - (model_started - started)))
                    finally:
                        response["metrics"]["model_seconds"] = round(time.perf_counter() - model_started, 4)
                        spec = request.to_dict()
                        response["request"] = spec
            confirm_appointments(request, query.get("appointment_choices"))
            spec = request.to_dict()
            response["request"] = spec
            response["issues"].extend(spec.get("issues", []))
            if not request.ready:
                response["status"] = "needs_clarification"
                return response
            # Explicit controls take priority over defaults inferred from text.
            if explicit_profile is None:
                text = spec.get("nature_language", "")
                if any(v in text for v in ("少走", "少步行")):
                    normalized = normalize_profile({"preset": "family"})
                elif any(v in text for v in ("别安排太满", "别排得太紧", "慢慢逛", "轻松")):
                    normalized = normalize_profile({"preset": "relaxed"})
                if "咖啡" in text and not any(v in text for v in ("不喝咖啡", "不想喝咖啡")):
                    normalized["restaurant_types"] = ["咖啡店"]
            for field, source in (("interest_types", "preferred_attraction_types"),
                                  ("restaurant_types", "preferred_restaurant_types")):
                normalized[field] = list(dict.fromkeys(normalized[field] + list(spec.get(source, ()))))
            normalized["hard_limits"].update(spec.get("profile", {}).get("hard_limits", {}))
            explicit_weights = (explicit_profile or {}).get("weights", {})
            for field in spec.get("preference_dimensions", ()):
                if field in normalized["weights"] and field not in explicit_weights:
                    normalized["weights"][field] = max(normalized["weights"][field], 3.)
            spec["profile"] = normalized
            response["status"] = "ready"
            response["metrics"]["interpretation_seconds"] = round(time.perf_counter() - started, 4)
        except (ValueError, TypeError) as error:
            if stage == "interpretation":
                response.update(status="planning_error", issues=[
                    *(response.get("request") or {}).get("issues", []),
                    {"code": "model_output_or_config", "message": "DeepSeek Flash 配置或输出有误，请重试或检查服务配置"},
                ])
                return response
            response.update(status="invalid_request" if stage == "input" else "planning_error")
            response["issues"].append({"code": "invalid_input" if stage == "input" else "module_contract_error",
                                       "message": str(error)})
        except Exception as error:
            if stage == "interpretation":
                response.update(status="planning_error", issues=[
                    *(response.get("request") or {}).get("issues", []),
                    {"code": "model_unavailable", "message": "DeepSeek Flash 未返回可用结果，请稍后重试"},
                ])
                return response
            response.update(status="planning_error")
            response["issues"].append({"code": type(error).__name__, "message": str(error)})
        finally:
            response["metrics"]["total_seconds"] = round(time.perf_counter() - started, 4)
            response["metrics"]["llm_calls"] = llm_calls
        return response


    def generate(self, query, max_seconds=15):
        prepared = self.prepare(query, max_seconds)
        if prepared["status"] != "ready":
            return prepared
        elapsed = prepared["metrics"]["total_seconds"]
        remaining = max_seconds - elapsed if prepared["metrics"]["llm_calls"] else max_seconds
        if remaining <= 0:
            prepared["status"] = "budget_exhausted"
            prepared["issues"].append({"code": "interpretation_budget", "message": "需求理解已耗尽本轮规划时间"})
            return prepared
        return self.build(prepared, remaining)

    def build(self, prepared, max_seconds, on_event=None):
        """Use an already reviewed request, with no interpretation during search."""
        from .engine import plan as build_plan
        from .validation import validate_plan
        from chinatravel.soft_constraints.delivery import tradeoff

        started = time.perf_counter()
        response = {**prepared, "issues": list(prepared["issues"]),
                    "metrics": dict(prepared["metrics"]), "alternatives": []}
        spec = prepared["request"]
        normalized = spec["profile"]
        assessed = {}
        first_sent = False

        def publish(stage, **content):
            if on_event is not None:
                on_event({"stage": stage, **content})

        def assess(candidate):
            try:
                key = json.dumps(candidate, cls=NpEncoder, allow_nan=False, sort_keys=True)
                if key not in assessed:
                    normalized_plan = json.loads(key)
                    check = validate_plan(normalized_plan, spec, self.env)
                    score = evaluate_preferences(normalized_plan, normalized, self.env, spec) if check["passed"] else None
                    assessed[key] = normalized_plan, check, score
                return assessed[key]
            except Exception as error:
                return None, {"passed": False, "issues": [{"code": "candidate_contract_error",
                    "message": str(error), "type": type(error).__name__}]}, None

        def first_candidate(candidate):
            nonlocal first_sent
            if first_sent:
                return
            verified, check, score = assess(candidate)
            if score and score["eligible"]:
                first_sent = True
                elapsed = round(time.perf_counter() - started, 4)
                response["metrics"]["first_valid_seconds"] = elapsed
                publish("comparing", first_valid_seconds=elapsed,
                        partial={"plan": verified, "validation": check, "personalization": score})

        def measures(item):
            metrics = item["personalization"]["metrics"]
            return dict(cost=metrics["total_cost"], walk_minutes=metrics["walking_minutes"],
                        access_minutes=metrics["transport_minutes"], visit_minutes=metrics["sightseeing_minutes"])

        try:
            publish("searching")
            kwargs = {"on_candidate": first_candidate} if on_event is not None else {}
            result = build_plan(spec, self.env, max_seconds=max_seconds, max_candidates=5, **kwargs)
            response["metrics"]["construction_seconds"] = round(time.perf_counter() - started, 4)
            response["metrics"].update(result.get("metrics", {}))
            response["diagnostics"] = result.get("diagnostics", {})
            candidates = result.get("candidates") or ([result["plan"]] if result.get("plan") else [])
            publish("validating", candidate_count=len(candidates))
            validation_started = time.perf_counter()
            valid, reviews = [], []
            for candidate in candidates:
                verified, check, score = assess(candidate)
                review = {"validation": check}
                if score is not None:
                    review["personalization"] = score
                    if score["eligible"]:
                        valid.append({**review, "plan": verified})
                reviews.append(review)
            response.update(candidate_count=len(candidates), candidate_reviews=reviews, valid_count=len(valid))
            if valid:
                valid.sort(key=lambda item: item["personalization"].get("score") or 0, reverse=True)
                selected, *others = valid
                response.update(status="success", **selected)
                if others:
                    for other in others:
                        difference = tradeoff(measures(selected), measures(other))
                        if difference is not None:
                            response["alternatives"].append({**other, "difference": difference})
                            break
                if not response["alternatives"]:
                    if len(valid) == 1:
                        reason = "本轮只核验通过一套方案。" if not response["metrics"].get("budget_exhausted") else "搜索时限已到，本轮只核验通过一套方案。"
                    else:
                        reason = f"已核验 {len(valid)} 套，未发现费用、步行、接驳或游览时间存在明显取舍的备选。"
                    response["comparison_note"] = reason
                response["issues"].append({"code": "cost_scope",
                    "message": "费用为已安排项目的快照估算，早餐自理、购物及未安排项目尚未计入。"})
                if selected["personalization"]["missing_preferences"]:
                    response["issues"].append({"code": "unmet_preferences",
                        "message": "尚未满足的偏好 " + "、".join(selected["personalization"]["missing_preferences"])})
            else:
                status = result.get("status", "no_feasible_plan")
                response["status"] = status if status != "success" else "no_feasible_plan"
                response["issues"].append({"code": "no_valid_candidate",
                    "message": "本轮未找到同时满足时间、费用和用户条件的方案。你可以调整条件，或重试。"})
                if spec.get("appointments"):
                    response["issues"].append({"code": "appointment_access",
                        "message": "本轮未核验出同时满足预约时段、当地接驳与返程缓冲的完整安排。请核对馆区、时段及当日可用交通。"})
            response["metrics"]["validation_selection_seconds"] = round(time.perf_counter() - validation_started, 4)
        except (ValueError, TypeError) as error:
            response.update(status="planning_error")
            response["issues"].append({"code": "module_contract_error", "message": str(error)})
        except Exception as error:
            response.update(status="planning_error")
            response["issues"].append({"code": type(error).__name__, "message": str(error)})
        finally:
            response["metrics"]["planning_seconds"] = round(time.perf_counter() - started, 4)
            response["metrics"]["total_seconds"] = round(
                prepared["metrics"]["total_seconds"] + response["metrics"]["planning_seconds"], 4)
        return response
