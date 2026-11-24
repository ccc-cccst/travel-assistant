from chinatravel.personalization.cli import main

if __name__=='__main__':
    try: raise SystemExit(main() or 0)
    except Exception as error:
        import json,sys
        from pathlib import Path
        from uuid import uuid4
        from chinatravel.personalization.lessons import describe_exception,record_failure,ROOT
        status=describe_exception(error,'cli')
        run=ROOT/'result/cli_errors'/uuid4().hex
        diagnostic=record_failure(run,status)
        print(json.dumps({**status,'diagnostic':diagnostic},ensure_ascii=False),file=sys.stderr)
        raise SystemExit(2)
