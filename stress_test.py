#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PDF OCR API 压力测试脚本
模仿 curl -s -X POST "$URL/parse/download" -F "file=@demo/demo1.pdf" -o output.zip
"""

import argparse
import time
import threading
import requests
import os
import sys
from queue import Queue
import statistics
from collections import defaultdict
import signal


class StressTest:
    def __init__(self, url, qps, pdf_path, duration=60, once_count=None):
        self.url = url
        self.qps = qps
        self.pdf_path = pdf_path
        self.duration = duration
        self.once_count = once_count  # 批量模式的请求数量
        
        # 统计数据
        self.request_times = []
        self.success_count = 0
        self.error_count = 0
        self.errors = defaultdict(int)
        self.start_time = None
        self.end_time = None
        
        # 控制变量
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        
        # 验证PDF文件
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF文件不存在: {pdf_path}")
    
    def make_request(self):
        """发送单个请求"""
        try:
            request_start = time.time()
            
            with open(self.pdf_path, 'rb') as f:
                files = {'file': f}
                response = requests.post(
                    f"{self.url}/parse/download",
                    files=files,
                    timeout=300  # 5分钟超时
                )
            
            request_end = time.time()
            request_time = request_end - request_start
            
            with self.lock:
                self.request_times.append(request_time)
                if response.status_code == 200:
                    self.success_count += 1
                else:
                    self.error_count += 1
                    self.errors[f"HTTP_{response.status_code}"] += 1
                    
        except requests.exceptions.Timeout:
            with self.lock:
                self.error_count += 1
                self.errors["Timeout"] += 1
        except requests.exceptions.ConnectionError:
            with self.lock:
                self.error_count += 1
                self.errors["ConnectionError"] += 1
        except Exception as e:
            with self.lock:
                self.error_count += 1
                self.errors[str(type(e).__name__)] += 1
    
    def worker_thread(self):
        """工作线程函数"""
        while not self.stop_event.is_set():
            self.make_request()
    
    def qps_controller(self):
        """QPS控制器"""
        interval = 1.0 / self.qps  # 每个请求之间的间隔
        threads = []
        
        print(f"开始压力测试...")
        print(f"目标URL: {self.url}/parse/download")
        print(f"PDF文件: {self.pdf_path}")
        print(f"目标QPS: {self.qps}")
        print(f"测试时长: {self.duration}秒")
        print("-" * 50)
        
        self.start_time = time.time()
        
        # 启动监控线程
        monitor_thread = threading.Thread(target=self.monitor)
        monitor_thread.daemon = True
        monitor_thread.start()
        
        while time.time() - self.start_time < self.duration and not self.stop_event.is_set():
            # 创建并启动新的请求线程
            thread = threading.Thread(target=self.make_request)
            thread.daemon = True
            thread.start()
            threads.append(thread)
            
            time.sleep(interval)
        
        self.end_time = time.time()
        self.stop_event.set()
        
        # 等待所有请求完成（最多等待30秒）
        print("\n等待剩余请求完成...")
        for thread in threads:
            thread.join(timeout=30)
    
    def monitor(self):
        """实时监控线程"""
        last_count = 0
        while not self.stop_event.is_set():
            time.sleep(5)  # 每5秒输出一次状态
            
            with self.lock:
                current_count = self.success_count + self.error_count
                current_success = self.success_count
                current_error = self.error_count
            
            if current_count > 0:
                elapsed = time.time() - self.start_time
                actual_qps = (current_count - last_count) / 5 if elapsed > 5 else current_count / elapsed
                
                print(f"[{elapsed:.1f}s] 总请求: {current_count}, 成功: {current_success}, "
                      f"失败: {current_error}, 实际QPS: {actual_qps:.2f}")
                
                last_count = current_count
    
    def batch_mode(self):
        """批量模式：一次性发送所有请求"""
        print(f"开始批量测试...")
        print(f"目标URL: {self.url}/parse/download")
        print(f"PDF文件: {self.pdf_path}")
        print(f"批量请求数: {self.once_count}")
        print("-" * 50)
        
        self.start_time = time.time()
        threads = []
        
        # 一次性启动所有请求线程
        for i in range(self.once_count):
            thread = threading.Thread(target=self.make_request)
            thread.daemon = True
            thread.start()
            threads.append(thread)
            
            if (i + 1) % 10 == 0:  # 每启动10个线程输出一次进度
                print(f"已启动 {i + 1}/{self.once_count} 个请求...")
        
        print(f"所有 {self.once_count} 个请求已启动，等待完成...")
        
        # 等待所有请求完成
        completed = 0
        for thread in threads:
            thread.join()
            completed += 1
            if completed % 10 == 0 or completed == len(threads):
                print(f"已完成 {completed}/{len(threads)} 个请求...")
        
        self.end_time = time.time()

    def run(self):
        """运行压力测试"""
        try:
            if self.once_count:
                self.batch_mode()
            else:
                self.qps_controller()
        except KeyboardInterrupt:
            print("\n收到中断信号，正在停止测试...")
            self.stop_event.set()
            self.end_time = time.time()
    
    def print_results(self):
        """打印测试结果"""
        print("\n" + "=" * 60)
        if self.once_count:
            print("批量测试结果")
        else:
            print("压力测试结果")
        print("=" * 60)
        
        actual_duration = self.end_time - self.start_time if self.end_time else 0
        total_requests = self.success_count + self.error_count
        
        print(f"测试时长: {actual_duration:.2f}秒")
        print(f"总请求数: {total_requests}")
        print(f"成功请求: {self.success_count}")
        print(f"失败请求: {self.error_count}")
        
        if total_requests > 0:
            success_rate = (self.success_count / total_requests) * 100
            print(f"成功率: {success_rate:.2f}%")
            
            if not self.once_count:  # QPS模式才显示QPS
                actual_qps = total_requests / actual_duration if actual_duration > 0 else 0
                print(f"实际QPS: {actual_qps:.2f}")
        
        if self.request_times:
            avg_time = statistics.mean(self.request_times)
            median_time = statistics.median(self.request_times)
            min_time = min(self.request_times)
            max_time = max(self.request_times)
            
            print(f"\n请求耗时统计:")
            print(f"  平均耗时: {avg_time:.3f}秒")
            print(f"  中位数耗时: {median_time:.3f}秒")
            print(f"  最小耗时: {min_time:.3f}秒")
            print(f"  最大耗时: {max_time:.3f}秒")
            
            if len(self.request_times) > 1:
                std_dev = statistics.stdev(self.request_times)
                print(f"  标准差: {std_dev:.3f}秒")
            
            if self.once_count:  # 批量模式特有的统计
                print(f"\n批量模式统计:")
                print(f"  并发度: {self.once_count}")
                print(f"  总吞吐量: {total_requests / actual_duration:.2f} 请求/秒" if actual_duration > 0 else "  总吞吐量: N/A")
        
        if self.errors:
            print(f"\n错误统计:")
            for error_type, count in self.errors.items():
                print(f"  {error_type}: {count}")


def signal_handler(signum, frame):
    """信号处理器"""
    print("\n收到中断信号，正在停止测试...")
    sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description='PDF OCR API 压力测试工具')
    parser.add_argument('url', help='API服务URL (例如: http://localhost:8000)')
    parser.add_argument('qps', type=int, help='目标QPS (每秒请求数)')
    parser.add_argument('pdf_path', help='PDF文件路径')
    parser.add_argument('-d', '--duration', type=int, default=60, 
                       help='测试持续时间（秒），默认60秒')
    parser.add_argument('--once', type=int, metavar='COUNT',
                       help='批量模式：一次性发送指定数量的请求，不使用QPS控制')
    
    args = parser.parse_args()
    
    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # 验证URL格式
        if not args.url.startswith('http'):
            args.url = 'http://' + args.url
        
        # 验证参数冲突
        if args.once and args.once <= 0:
            print("错误: --once 参数必须是正整数")
            sys.exit(1)
        
        # 创建并运行测试
        test = StressTest(args.url, args.qps, args.pdf_path, args.duration, args.once)
        test.run()
        test.print_results()
        
    except FileNotFoundError as e:
        print(f"错误: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"意外错误: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()

