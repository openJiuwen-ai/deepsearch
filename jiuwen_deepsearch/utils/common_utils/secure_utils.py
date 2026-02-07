# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

def secure_clear_memory(ba: bytearray):
    '''
    Securely clear the contents of a bytearray from memory.
    '''
    for i, _ in enumerate(ba):
        ba[i] = 0
