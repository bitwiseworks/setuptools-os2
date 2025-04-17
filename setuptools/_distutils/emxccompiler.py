from .compilers.C import emx
from .compilers.C.emx import (
    CONFIG_H_NOTOK,
    CONFIG_H_OK,
    CONFIG_H_UNCERTAIN,
    check_config_h,
)

__all__ = [
    'CONFIG_H_NOTOK',
    'CONFIG_H_OK',
    'CONFIG_H_UNCERTAIN',
    'CygwinCCompiler',
    'Mingw32CCompiler',
    'check_config_h',
]


EMXCCompiler = emx.Compiler
