"""Merge the selected adapter into the exact official base, then export Q8 GGUF."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('experiment', type=Path)
    parser.add_argument('--converter', type=Path, required=True)
    args = parser.parse_args()
    root = args.experiment.resolve()
    destination = root / 'deployment_export'
    destination.mkdir(exist_ok=True)
    import torch
    from transformers import AutoTokenizer, Qwen3_5ForCausalLM
    from peft import PeftModel
    adapter = root / 'outputs/bf16_rank8/adapter'
    base = Qwen3_5ForCausalLM.from_pretrained(
        root / 'base_model', local_files_only=True, dtype=torch.bfloat16,
        device_map={'': 0}, attn_implementation='sdpa',
    )
    model = PeftModel.from_pretrained(base, adapter)
    with torch.no_grad():
        merged = model.merge_and_unload(safe_merge=True)
    merged.config.use_cache = True
    # The trained causal language model has 32 decoder layers and no MTP head.
    merged.config.mtp_num_hidden_layers = 0
    tokenizer = AutoTokenizer.from_pretrained(root / 'base_model', local_files_only=True)
    merged.config.eos_token_id = tokenizer.eos_token_id
    merged.generation_config.eos_token_id = tokenizer.eos_token_id
    merged.generation_config.pad_token_id = tokenizer.eos_token_id
    merged_dir = destination / 'merged_bf16'
    merged.save_pretrained(merged_dir, safe_serialization=True, max_shard_size='2GB')
    tokenizer.save_pretrained(merged_dir)
    del merged, model, base
    torch.cuda.empty_cache()
    output = destination / 'Qwen3.5-4B-SoftDSL-v2-Q8_0.gguf'
    subprocess.run([
        sys.executable, str(args.converter), str(merged_dir),
        '--outtype', 'q8_0', '--outfile', str(output), '--no-mtp',
    ], check=True)
    with output.open('rb') as stream:
        sha256 = hashlib.file_digest(stream, 'sha256').hexdigest()
    manifest = dict(file=output.name, size=output.stat().st_size, sha256=sha256,
                    adapter_checkpoint=60, base_model='Qwen/Qwen3.5-4B',
                    quantization='Q8_0', merged_precision='bfloat16')
    (destination / 'model_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(manifest), flush=True)


if __name__ == '__main__':
    main()
