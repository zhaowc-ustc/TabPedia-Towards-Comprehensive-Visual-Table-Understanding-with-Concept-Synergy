# Copyright (c) OpenMMLab. All rights reserved.
import logging
import os
import types

import torch
import transformers
from mmengine import print_log
from mmengine.utils import digit_version

IS_LOW_VERSION_TRANSFORMERS = digit_version(
    transformers.__version__) < digit_version('4.36')
SUPPORT_FLASH1 = digit_version(torch.__version__) >= digit_version('2.0.0')
SUPPORT_FLASH2 = False

try:
    from flash_attn import flash_attn_func  # pre-check # noqa: F401

    SUPPORT_FLASH2 = True
except ImportError:
    pass

SUPPORT_FLASH = SUPPORT_FLASH1 or SUPPORT_FLASH2

USE_TRITON_KERNEL = bool(os.getenv('USE_TRITON_KERNEL', default=0))
SUPPORT_TRITON = False
try:
    import triton  # pre-check # noqa: F401
    import triton.language as tl  # pre-check # noqa: F401
    SUPPORT_TRITON = True
except ImportError:
    if USE_TRITON_KERNEL:
        raise RuntimeError(
            'USE_TRITON_KERNEL is set to 1, but triton has not been installed.'
            ' Run `pip install triton==2.1.0` to install triton.')

NO_ATTN_WEIGHTS_MSG = (
    'Due to the implementation of the PyTorch version of flash attention, '
    'even when the `output_attentions` flag is set to True, it is not '
    'possible to return the `attn_weights`.')



def dispatch_internlm2_attn_forward(model, use_varlen_attn):
    if use_varlen_attn:
        assert SUPPORT_FLASH2 and SUPPORT_TRITON, \
            'flash_attn and triton is required if you want to use varlen_attn.'
    elif not SUPPORT_FLASH:
        return

    from .internlm2 import (internlm2_attn_forward,
                            internlm2_varlen_attn_forward)

    print_log(NO_ATTN_WEIGHTS_MSG, 'current', logging.WARNING)
    for module in model.modules():
        if type(module).__name__ == 'InternLM2Attention':
            if use_varlen_attn:
                print_log('dispatch internlm2 varlen attn forward', 'current')
                module.forward = types.MethodType(
                    internlm2_varlen_attn_forward, module)
            else:
                print_log('dispatch internlm2 attn forward', 'current')
                module.forward = types.MethodType(internlm2_attn_forward,
                                                  module)


def dispatch_internlm_rmsnorm_forward(model):
    if not SUPPORT_TRITON:
        return

    from .triton_kernels import rms_norm_forward

    for module in model.modules():
        if type(module).__name__ == 'InternLMRMSNorm':
            print_log('dispatch internlm rmsnorm forward', 'current')
            module.forward = types.MethodType(rms_norm_forward, module)


def dispatch_internlm2_rmsnorm_forward(model):
    if not SUPPORT_TRITON:
        return

    from .triton_kernels import rms_norm_forward

    for module in model.modules():
        if type(module).__name__ == 'InternLM2RMSNorm':
            print_log('dispatch internlm2 rmsnorm forward', 'current')
            module.forward = types.MethodType(rms_norm_forward, module)


def replace_internlm2_rote(model):
    from .internlm2 import InternLM2RotaryEmbedding

    rotary_base = model.config.rope_theta

    def traverse(module):
        for name, child in module.named_children():
            if type(child).__name__ in (
                    'InternLM2RotaryEmbedding',
                    'InternLM2DynamicNTKScalingRotaryEmbedding'):
                print_log('replace internlm2 rope', 'current')
                dim_model = child.inv_freq.shape[0] * 2
                child_new = InternLM2RotaryEmbedding(
                    dim_model, child.max_seq_len_cached, rotary_base).to(
                        device=child.inv_freq.device,
                        dtype=child.inv_freq.dtype)
                setattr(module, name, child_new)
            else:
                traverse(child)

    traverse(model)




def dispatch_modules(model, use_varlen_attn=False):
    model_name = model.__class__.__name__.lower()
    if 'internlm2' in model_name:
        dispatch_internlm2_attn_forward(model, use_varlen_attn)
        if USE_TRITON_KERNEL:
            dispatch_internlm2_rmsnorm_forward(model)
        replace_internlm2_rote(model)

__all__ = ['dispatch_modules']
