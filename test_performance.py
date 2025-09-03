#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MonkeyOCR性能测试脚本
"""

import time
import requests
import argparse
import os
from typing import Optional

def test_server_performance(server_url: str = "http://localhost:8000", 
                          test_file: str = None):
    """测试服务器性能"""
    
    print("🚀 开始MonkeyOCR性能测试...")
    print(f"📍 服务器地址: {server_url}")
    
    # 检查服务器状态
    try:
        response = requests.get(f"{server_url}/health", timeout=5)
        if response.status_code != 200:
            print("❌ 服务器不可用")
            return
        
        health_data = response.json()
        print(f"✅ 服务器状态: {health_data.get('status')}")
        print(f"✅ 模型已加载: {health_data.get('model_loaded')}")
        print(f"🎮 使用设备: {health_data.get('device', 'unknown')}")
        print()
        
    except Exception as e:
        print(f"❌ 连接服务器失败: {e}")
        return
    
    # 准备测试文件
    if test_file is None:
        print("⚠️  未指定测试文件，将跳过文档解析测试")
        return
    
    if not os.path.exists(test_file):
        print(f"❌ 测试文件不存在: {test_file}")
        return
    
    print(f"📄 测试文件: {test_file}")
    
    # 开始性能测试
    start_time = time.time()
    
    try:
        with open(test_file, 'rb') as f:
            files = {'file': (os.path.basename(test_file), f, 'application/pdf')}
            data = {'task_type': 'all'}
            
            print("🕐 开始解析文档...")
            response = requests.post(
                f"{server_url}/parse", 
                files=files, 
                data=data,
                timeout=300  # 5分钟超时
            )
        
        total_time = time.time() - start_time
        
        if response.status_code == 200:
            result = response.json()
            print("✅ 文档解析完成!")
            print(f"⏱️  总耗时: {total_time:.2f}秒")
            
            # 显示详细性能数据
            if 'performance' in result:
                perf = result['performance']
                print("\n📊 详细性能报告:")
                print("=" * 50)
                for key, value in perf.items():
                    print(f"{key}: {value}")
                print("=" * 50)
                
                # 生成性能分析
                if 'optimization_suggestions' in result:
                    print("\n🚀 优化建议:")
                    for suggestion in result['optimization_suggestions']:
                        print(f"  {suggestion}")
            
            # 显示生成的文件
            if 'files' in result:
                print(f"\n📁 生成文件数: {len(result['files'])}")
                for file in result['files'][:5]:  # 显示前5个文件
                    print(f"  - {file}")
                if len(result['files']) > 5:
                    print(f"  ... 还有 {len(result['files']) - 5} 个文件")
            
        else:
            print(f"❌ 解析失败: {response.status_code}")
            print(f"错误信息: {response.text}")
            
    except requests.exceptions.Timeout:
        print("❌ 请求超时 (>5分钟)")
    except Exception as e:
        print(f"❌ 解析出错: {e}")

def benchmark_multiple_files(server_url: str, file_list: list, iterations: int = 1):
    """批量性能基准测试"""
    
    print(f"🏁 开始批量性能测试 ({iterations}次迭代)")
    print(f"📄 测试文件数: {len(file_list)}")
    
    total_times = []
    
    for iteration in range(iterations):
        print(f"\n🔄 第 {iteration + 1}/{iterations} 次迭代")
        iteration_times = []
        
        for i, test_file in enumerate(file_list):
            print(f"\n📄 处理文件 {i+1}/{len(file_list)}: {os.path.basename(test_file)}")
            
            start_time = time.time()
            
            try:
                with open(test_file, 'rb') as f:
                    files = {'file': (os.path.basename(test_file), f, 'application/pdf')}
                    data = {'task_type': 'all'}
                    
                    response = requests.post(
                        f"{server_url}/parse", 
                        files=files, 
                        data=data,
                        timeout=300
                    )
                
                elapsed = time.time() - start_time
                iteration_times.append(elapsed)
                
                if response.status_code == 200:
                    print(f"✅ 完成: {elapsed:.2f}秒")
                else:
                    print(f"❌ 失败: {response.status_code}")
                    
            except Exception as e:
                print(f"❌ 错误: {e}")
                iteration_times.append(float('inf'))
        
        total_times.extend(iteration_times)
        avg_time = sum(iteration_times) / len(iteration_times) if iteration_times else 0
        print(f"🏁 本轮平均耗时: {avg_time:.2f}秒")
    
    # 统计结果
    valid_times = [t for t in total_times if t != float('inf')]
    if valid_times:
        print("\n📊 最终性能统计:")
        print("=" * 40)
        print(f"总处理文件数: {len(valid_times)}")
        print(f"平均处理时间: {sum(valid_times)/len(valid_times):.2f}秒")
        print(f"最快处理时间: {min(valid_times):.2f}秒")
        print(f"最慢处理时间: {max(valid_times):.2f}秒")
        print(f"总处理时间: {sum(valid_times):.2f}秒")
        print("=" * 40)

def main():
    parser = argparse.ArgumentParser(description="MonkeyOCR性能测试工具")
    parser.add_argument("--server", default="http://localhost:8000", 
                       help="服务器地址 (默认: http://localhost:8000)")
    parser.add_argument("--file", help="测试文件路径")
    parser.add_argument("--files", nargs="+", help="批量测试文件路径")
    parser.add_argument("--iterations", type=int, default=1, 
                       help="测试迭代次数 (默认: 1)")
    
    args = parser.parse_args()
    
    if args.files:
        # 批量测试
        benchmark_multiple_files(args.server, args.files, args.iterations)
    elif args.file:
        # 单文件测试
        test_server_performance(args.server, args.file)
    else:
        # 仅测试服务器状态
        test_server_performance(args.server)

if __name__ == "__main__":
    main() 