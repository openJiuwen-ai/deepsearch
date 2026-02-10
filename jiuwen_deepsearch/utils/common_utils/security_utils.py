# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.


def zero_secret(ba: bytearray):
    '''
    将存储在可变 bytearray 中的敏感数据（如密钥）清零。
    '''
    for i, _ in enumerate(ba):
        ba[i] = 0
