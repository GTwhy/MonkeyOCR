#!/bin/bash

# MonkeyOCR 服务启动脚本

# 激活 conda 环境
echo "🐍 激活 conda 环境: MonkeyOCR"
if command -v conda &> /dev/null; then
    # 初始化 conda（如果需要）
    eval "$(conda shell.bash hook)"
    
    # 检查环境是否存在
    if conda env list | grep -q "MonkeyOCR"; then
        conda activate MonkeyOCR
        echo "✅ 成功激活 MonkeyOCR 环境"
    else
        echo "⚠️  警告: MonkeyOCR conda 环境不存在"
        echo "请先创建环境: conda create -n MonkeyOCR python=3.8"
        echo "或使用当前Python环境继续..."
    fi
else
    echo "⚠️  警告: conda 未安装，使用当前Python环境"
fi


# 默认配置
HOST=${HOST:-"0.0.0.0"}
PORT=${PORT:-7861}
CONFIG=${CONFIG:-"yolo_model_configs.yaml"}
WORKERS=${WORKERS:-1}
SERVER_TYPE=${SERVER_TYPE:-"api"}  # 新增：服务器类型选择

# 智能选择GPU设备
if [ -z "$CUDA_DEVICE" ]; then
    # 如果没有指定CUDA_DEVICE，尝试自动选择
    if command -v nvidia-smi > /dev/null; then
        # 获取内存使用最少的GPU
        CUDA_DEVICE=$(nvidia-smi --query-gpu=index,memory.used --format=csv,noheader,nounits | sort -k2 -n | head -1 | cut -d',' -f1 | tr -d ' ')
        echo "🤖 自动选择GPU: $CUDA_DEVICE (内存使用最少)"
    else
        echo "⚠️  nvidia-smi不可用，使用CPU模式"
        CUDA_DEVICE=""
    fi
else
    echo "🎯 使用指定GPU: $CUDA_DEVICE"
fi

echo "🚀 启动MonkeyOCR服务..."
echo "📍 监听地址: $HOST:$PORT"
echo "⚙️  配置文件: $CONFIG"
echo "👷 工作进程: $WORKERS"
echo "🎮 GPU设备: $CUDA_DEVICE"
echo "🔧 服务器类型: $SERVER_TYPE"
echo ""

# 检查配置文件
if [ ! -f "$CONFIG" ]; then
    echo "❌ 配置文件不存在: $CONFIG"
    echo "请确保配置文件存在，或设置CONFIG环境变量"
    exit 1
fi

# 从配置文件中读取 models_dir
echo "📖 读取配置文件中的模型目录..."
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    echo "❌ 未找到 Python 解释器"
    exit 1
fi

# 使用Python读取YAML配置文件中的models_dir
MODELS_DIR=$($PYTHON_CMD -c "
import yaml
import sys
try:
    with open('$CONFIG', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    models_dir = config.get('models_dir', 'model_weight')
    print(models_dir)
except Exception as e:
    print('model_weight', file=sys.stderr)
    sys.exit(0)
" 2>/dev/null)

# 如果读取失败，使用默认值
if [ -z "$MODELS_DIR" ]; then
    MODELS_DIR="model_weight"
    echo "⚠️  无法读取配置文件中的models_dir，使用默认值: $MODELS_DIR"
else
    echo "📂 配置的模型目录: $MODELS_DIR"
fi

# 检查模型权重目录
if [ ! -d "$MODELS_DIR" ]; then
    echo "❌ 错误: 模型目录不存在: $MODELS_DIR"
    echo "请确保模型权重已下载到 $MODELS_DIR 目录"
    echo "或检查配置文件 $CONFIG 中的 models_dir 设置是否正确"
    exit 1
else
    echo "✅ 模型目录检查通过: $MODELS_DIR"
fi

# 启动服务
export MONKEYOCR_CONFIG="$CONFIG"
export CUDA_DEVICE="$CUDA_DEVICE"

# 设置CUDA可见设备（仅在指定了GPU时）
if [ -n "$CUDA_DEVICE" ]; then
    export CUDA_VISIBLE_DEVICES="$CUDA_DEVICE"
    echo "🔧 设置环境变量:"
    echo "   CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES" 
    echo "   CUDA_DEVICE=$CUDA_DEVICE"
    
    # 验证GPU可用性
    if command -v nvidia-smi > /dev/null; then
        if nvidia-smi -i $CUDA_DEVICE > /dev/null 2>&1; then
            echo "✅ GPU $CUDA_DEVICE 可用"
        else
            echo "❌ GPU $CUDA_DEVICE 不可用，将自动降级到CPU模式"
            unset CUDA_VISIBLE_DEVICES
            export CUDA_DEVICE=""
        fi
    fi
else
    echo "💻 将使用CPU模式"
fi
echo ""

# 根据服务器类型选择启动方式
if [ "$SERVER_TYPE" = "api" ]; then
    echo "🔥 启动FastAPI服务器..."
    if [ ! -f "api/main.py" ]; then
        echo "❌ api/main.py 文件不存在"
        exit 1
    fi
    python api/main.py --host "$HOST" --port "$PORT" --config "$CONFIG"
else
    echo "🔥 启动解析服务器..."
    if [ ! -f "parse_server.py" ]; then
        echo "❌ parse_server.py 文件不存在"
        exit 1
    fi
    python parse_server.py \
        --host "$HOST" \
        --port "$PORT" \
        --config "$CONFIG" \
        --workers "$WORKERS"
fi 