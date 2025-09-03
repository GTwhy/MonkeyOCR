# MonkeyOCR API 部署与压测指南

## 📋 概述

本文档记录了MonkeyOCR API的完整部署方案，包括本地服务、公网访问配置、压力测试工具的开发和使用。

## 🚀 部署状态

### 本地API服务
- **服务地址**: http://localhost:7861
- **进程ID**: 2338159
- **启动命令**: `python api/main.py`
- **状态**: ✅ 运行中
- **主要接口**: `/parse/download` (POST)

### 公网访问配置
- **ngrok管理界面**: http://localhost:4040
- **公网地址**: https://49b73e67fcae.ngrok-free.app
- **状态**: ✅ 已配置并测试通过

## 🛠️ ngrok配置详情

### 当前Tunnel配置
```json
{
  "name": "ocr-api",
  "public_url": "https://49b73e67fcae.ngrok-free.app",
  "proto": "https",
  "config": {
    "addr": "http://localhost:7861",
    "inspect": true
  }
}
```

### 管理命令
```bash
# 查看所有tunnel状态
curl -s http://localhost:4040/api/tunnels | python3 -m json.tool

# 创建新tunnel
curl -X POST http://localhost:4040/api/tunnels \
  -H "Content-Type: application/json" \
  -d '{"addr":"http://localhost:7861","proto":"http","name":"ocr-api"}'

# 删除tunnel
curl -X DELETE http://localhost:4040/api/tunnels/ocr-api
```

## 📊 压力测试工具

### 工具概述
开发了专用的Python压测脚本 `stress_test.py`，支持两种测试模式：

#### 1. QPS模式（持续压测）
- 按指定QPS持续发送请求
- 实时监控测试状态
- 适合测试长时间稳定性

#### 2. 批量模式（并发测试）
- 一次性发送所有请求
- 测试峰值并发处理能力
- 计算准确的平均响应时间

### 功能特性
- 🚀 支持自定义QPS和并发数
- 📦 支持批量模式（--once参数）
- ⏱️ 实时监控测试状态
- 📊 详细的统计信息（平均耗时、成功率等）
- 🛡️ 完善的错误处理
- 🔄 支持中断恢复

### 使用方法

#### 基本语法
```bash
python3 stress_test.py <URL> <QPS> <PDF文件路径> [选项]
```

#### 参数说明
- `URL`: API服务的URL
- `QPS`: 目标每秒请求数（批量模式下不使用但需提供）
- `PDF文件路径`: 要上传的PDF文件路径
- `-d, --duration`: 测试持续时间（秒），默认60秒
- `--once COUNT`: 批量模式，一次性发送指定数量的请求

#### 使用示例

##### QPS模式示例
```bash
# 基本压测（5 QPS，60秒）
python3 stress_test.py https://49b73e67fcae.ngrok-free.app 5 ./demo/demo1.pdf

# 自定义时长（10 QPS，120秒）
python3 stress_test.py https://49b73e67fcae.ngrok-free.app 10 ./demo/demo1.pdf -d 120

# 本地测试
python3 stress_test.py http://localhost:7861 8 ./demo/demo1.pdf
```

##### 批量模式示例
```bash
# 发送100个并发请求
python3 stress_test.py https://49b73e67fcae.ngrok-free.app 0 ./demo/demo1.pdf --once 100

# 发送50个并发请求
python3 stress_test.py https://49b73e67fcae.ngrok-free.app 0 ./demo/demo1.pdf --once 50

# 小规模测试（2个请求）
python3 stress_test.py https://49b73e67fcae.ngrok-free.app 1 ./demo/demo1.pdf --once 2
```

## 📈 测试结果示例

### 批量模式测试结果
```
============================================================
批量测试结果
============================================================
测试时长: 38.04秒
总请求数: 2
成功请求: 2
失败请求: 0
成功率: 100.00%

请求耗时统计:
  平均耗时: 31.224秒
  中位数耗时: 31.224秒
  最小耗时: 24.408秒
  最大耗时: 38.040秒
  标准差: 9.639秒

批量模式统计:
  并发度: 2
  总吞吐量: 0.05 请求/秒
```

### QPS模式测试结果示例
```
============================================================
压力测试结果
============================================================
测试时长: 60.12秒
总请求数: 300
成功请求: 295
失败请求: 5
成功率: 98.33%
实际QPS: 4.99

请求耗时统计:
  平均耗时: 15.234秒
  中位数耗时: 14.567秒
  最小耗时: 12.123秒
  最大耗时: 23.456秒
  标准差: 2.345秒

错误统计:
  Timeout: 3
  HTTP_500: 2
```

## 🔗 API使用方法

### 基本curl命令
```bash
# 使用公网地址
curl -X POST "https://49b73e67fcae.ngrok-free.app/parse/download" \
     -F "file=@demo/demo1.pdf" \
     -o output.zip

# 使用本地地址
curl -X POST "http://localhost:7861/parse/download" \
     -F "file=@demo/demo1.pdf" \
     -o output.zip
```

### 环境变量使用
```bash
export URL="https://49b73e67fcae.ngrok-free.app"
curl -X POST "$URL/parse/download" -F "file=@demo/demo1.pdf" -o output.zip
```

## ⚠️ 注意事项

### ngrok相关
1. **免费版限制**: ngrok免费版有流量和连接数限制
2. **地址变化**: 重启ngrok后公网地址会变化
3. **日志监控**: 可在管理界面查看详细请求日志
4. **安全性**: 公网地址对所有人开放，注意安全

### 压测相关
1. **资源消耗**: 高并发测试会消耗大量系统资源
2. **网络影响**: 测试结果受网络延迟影响
3. **服务负载**: 避免对生产服务进行大规模压测
4. **文件大小**: 大PDF文件会显著影响测试时间

### 批量模式特别注意
- 建议批量请求数量不超过1000
- 一次性创建大量线程可能消耗大量内存
- 适合测试并发处理能力，不适合测试持续性能

## 🛠️ 故障排除

### 常见错误
- `FileNotFoundError`: PDF文件不存在，检查文件路径
- `ConnectionError`: 无法连接到API服务，检查URL和服务状态
- `Timeout`: 请求超时，可能是服务响应慢或网络问题
- `HTTP_404`: API端点不存在，检查URL路径

### 性能调优建议
- 对于高QPS测试，监控系统资源使用情况
- 如果出现大量超时，考虑降低QPS或增加超时时间
- 确保目标服务有足够的处理能力
- 批量模式下如果出现内存不足，减少并发请求数量

## 📁 文件结构

```
MonkeyOCR/
├── api/main.py                    # API服务主文件
├── stress_test.py                 # 压力测试脚本
├── STRESS_TEST_README.md          # 压测工具详细文档
├── OCR_API_DEPLOYMENT_AND_TEST_GUIDE.md.md        # 本文档
├── demo/
│   ├── demo1.pdf                  # 测试PDF文件
│   └── demo2.pdf                  # 测试PDF文件
└── ...
```

## 🔄 快速启动流程

### 1. 启动API服务
```bash
# run at root of MonkeyOCR
CUDA_VISIBLE_DEVICES=X MONKEYOCR_CONFIG=batch_model_configs.yaml nohup python api/main.py > monkey_ocr_working.log 2>&1 &
```

### 2. 配置ngrok（如需公网访问）
```bash
# ngrok应该已经在运行，如果没有：
ngrok http 7861
```

### 3. 验证服务
```bash
# 本地测试
curl -I http://localhost:7861/

# 公网测试
curl -I https://49b73e67fcae.ngrok-free.app/
```

### 4. 运行压测
```bash
# 批量模式测试
python3 stress_test.py https://49b73e67fcae.ngrok-free.app 1 ./demo/demo1.pdf --once 10

# QPS模式测试
python3 stress_test.py https://49b73e67fcae.ngrok-free.app 5 ./demo/demo1.pdf -d 30
```

## 📞 联系信息

如有问题，请检查：
1. 服务进程是否正常运行
2. ngrok tunnel是否正确配置
3. 网络连接是否正常
4. PDF文件是否存在且可读

---

**文档更新时间**: 2025-08-26
**API版本**: MonkeyOCR v1.0
**ngrok版本**: Free tier
