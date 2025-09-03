#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简单的性能监控测试脚本
"""

import time
import requests
import os

def test_performance_monitoring():
    """测试性能监控功能"""
    
    print("🧪 测试MonkeyOCR性能监控功能")
    print("=" * 50)
    
    # 检查服务状态
    try:
        response = requests.get("http://localhost:8000/health", timeout=5)
        if response.status_code != 200:
            print("❌ 服务器不可用")
            return False
            
        print("✅ 服务器运行正常")
        
    except Exception as e:
        print(f"❌ 连接服务器失败: {e}")
        return False
    
    # 测试文档处理
    test_files = [
        "test_3.pdf",
        "output/test_1.pdf"  # 如果存在的话
    ]
    
    for test_file in test_files:
        if os.path.exists(test_file):
            print(f"\n📄 测试文件: {test_file}")
            
            try:
                start_time = time.time()
                
                with open(test_file, 'rb') as f:
                    files = {'file': (os.path.basename(test_file), f, 'application/pdf')}
                    data = {'task_type': 'all', 'output_format': 'json'}
                    
                    print("🕐 开始解析...")
                    response = requests.post(
                        "http://localhost:8000/parse", 
                        files=files, 
                        data=data,
                        timeout=300
                    )
                
                total_time = time.time() - start_time
                
                if response.status_code == 200:
                    result = response.json()
                    print(f"✅ 解析完成! 总耗时: {total_time:.2f}秒")
                    
                    # 检查是否有性能数据
                    if 'performance_summary' in result and result['performance_summary']:
                        print("\n📊 性能数据:")
                        for key, value in result['performance_summary'].items():
                            print(f"  {key}: {value}")
                    else:
                        print("⚠️  响应中没有性能数据")
                    
                    # 检查优化建议
                    if 'optimization_suggestions' in result and result['optimization_suggestions']:
                        print("\n🚀 优化建议:")
                        for suggestion in result['optimization_suggestions']:
                            print(f"  - {suggestion}")
                    else:
                        print("⚠️  响应中没有优化建议")
                    
                    return True
                    
                else:
                    print(f"❌ 解析失败: {response.status_code}")
                    print(f"错误: {response.text}")
                    return False
                    
            except Exception as e:
                print(f"❌ 测试失败: {e}")
                return False
    
    print("⚠️  没有找到测试文件")
    return False

def check_performance_monitor_import():
    """检查性能监控模块是否能正确导入"""
    
    print("\n🔍 检查性能监控模块...")
    
    try:
        from performance_monitor import global_monitor
        print("✅ performance_monitor 模块导入成功")
        
        # 测试性能监控器
        global_monitor.reset()
        
        with global_monitor.time_phase("测试阶段"):
            time.sleep(0.1)  # 模拟工作
        
        summary = global_monitor.get_summary()
        print("✅ 性能监控器工作正常")
        print(f"  测试统计: {summary}")
        
        return True
        
    except Exception as e:
        print(f"❌ 性能监控模块有问题: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("🚀 MonkeyOCR性能监控测试")
    print("=" * 60)
    
    # 测试1: 检查模块导入
    import_ok = check_performance_monitor_import()
    
    if import_ok:
        # 测试2: 检查服务性能监控
        service_ok = test_performance_monitoring()
        
        if service_ok:
            print("\n🎉 性能监控功能正常!")
        else:
            print("\n⚠️  服务性能监控可能有问题")
    else:
        print("\n❌ 性能监控模块有问题")
    
    print("\n💡 提示:")
    print("如果看不到详细的性能报告，请检查:")
    print("1. 服务器的日志输出（在启动服务的终端中）")
    print("2. 确保使用了 print() 而不仅仅是 logger")
    print("3. 检查 parse_server.py 中是否正确调用了 global_monitor.print_detailed_report()") 