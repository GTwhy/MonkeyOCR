#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试GPU设置是否正确
"""

import os
import sys
import torch

def test_gpu_setup():
    """测试GPU设置"""
    
    print("🔍 GPU环境测试")
    print("=" * 50)
    
    # 检查PyTorch和CUDA
    print(f"PyTorch版本: {torch.__version__}")
    print(f"CUDA可用: {torch.cuda.is_available()}")
    
    if not torch.cuda.is_available():
        print("❌ CUDA不可用，请检查环境配置")
        return False
    
    # 检查GPU数量
    num_gpus = torch.cuda.device_count()
    print(f"可用GPU数量: {num_gpus}")
    
    # 显示所有GPU信息
    for i in range(num_gpus):
        name = torch.cuda.get_device_name(i)
        props = torch.cuda.get_device_properties(i)
        memory_gb = props.total_memory / 1024**3
        print(f"  GPU {i}: {name} ({memory_gb:.1f}GB)")
    
    # 测试环境变量
    cuda_visible = os.getenv("CUDA_VISIBLE_DEVICES", "未设置")
    cuda_device = os.getenv("CUDA_DEVICE", "未设置")
    print(f"CUDA_VISIBLE_DEVICES: {cuda_visible}")
    print(f"CUDA_DEVICE: {cuda_device}")
    
    # 测试GPU选择逻辑
    try:
        if cuda_visible != "未设置":
            # 如果设置了CUDA_VISIBLE_DEVICES，当前设备应该是0
            torch.cuda.set_device(0)
            current_device = torch.cuda.current_device()
            print(f"当前使用设备: {current_device}")
            
            # 测试内存分配
            x = torch.randn(100, 100).cuda()
            memory_allocated = torch.cuda.memory_allocated() / 1024**2
            print(f"✅ GPU测试成功，已分配内存: {memory_allocated:.1f}MB")
            
            # 清理
            del x
            torch.cuda.empty_cache()
            return True
            
        else:
            print("⚠️  未设置CUDA_VISIBLE_DEVICES")
            return False
            
    except Exception as e:
        print(f"❌ GPU测试失败: {e}")
        return False

def test_model_initialization():
    """测试模型初始化过程"""
    
    print("\n🧠 模型初始化测试")
    print("=" * 50)
    
    try:
        # 导入模型初始化函数
        from parse_server import initialize_model
        
        # 测试模型初始化
        gpu_id = int(os.getenv("CUDA_DEVICE", "9"))
        print(f"尝试在GPU {gpu_id}上初始化模型...")
        
        model = initialize_model("model_configs.yaml", gpu_id)
        
        if model is not None:
            print("✅ 模型初始化成功!")
            print(f"模型设备: {getattr(model, 'device', '未知')}")
            return True
        else:
            print("❌ 模型初始化失败")
            return False
            
    except Exception as e:
        print(f"❌ 模型初始化错误: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("🚀 MonkeyOCR GPU设置测试")
    print("=" * 60)
    
    # 测试1: GPU基础环境
    gpu_ok = test_gpu_setup()
    
    if gpu_ok:
        # 测试2: 模型初始化（可选）
        if len(sys.argv) > 1 and sys.argv[1] == "--test-model":
            model_ok = test_model_initialization()
            
            if model_ok:
                print("\n🎉 所有测试通过!")
            else:
                print("\n❌ 模型测试失败")
                sys.exit(1)
        else:
            print("\n✅ GPU环境测试通过!")
            print("如需测试模型初始化，请运行: python test_gpu_setup.py --test-model")
    else:
        print("\n❌ GPU环境测试失败")
        sys.exit(1) 