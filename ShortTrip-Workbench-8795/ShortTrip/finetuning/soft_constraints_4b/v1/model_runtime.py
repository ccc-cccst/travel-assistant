"""Use the numerically verified PyTorch Gated DeltaNet implementation."""
import inspect

def use_reference_attention():
    from transformers.models.qwen3_5 import modeling_qwen3_5
    for name in ('torch_chunk_gated_delta_rule','torch_recurrent_gated_delta_rule'):
        function=getattr(modeling_qwen3_5,name)
        setattr(modeling_qwen3_5,name,inspect.unwrap(function))
