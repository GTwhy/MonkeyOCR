#!/usr/bin/env python3
# Copyright (c) Opendatalab. All rights reserved.
import os
import time
import logging
import asyncio
from pathlib import Path
from typing import Optional, List
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import torch.distributed as dist
from pdf2image import convert_from_path
import uvicorn
from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel
import tempfile
import shutil
import zipfile

from magic_pdf.data.data_reader_writer import FileBasedDataWriter, FileBasedDataReader
from magic_pdf.data.dataset import PymuDocDataset, ImageDataset
from magic_pdf.model.doc_analyze_by_custom_model_llm import doc_analyze_llm
from magic_pdf.model.custom_model import MonkeyOCR
from performance_monitor import PerformanceMonitor, global_monitor

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 定义任务指令
TASK_INSTRUCTIONS = {
    'text': 'Please output the text content from the image.',
    'formula': 'Please write out the expression of the formula in the image using LaTeX format.',
    'table': 'Please output the table in the image in LaTeX format.'
}

# 全局模型实例和执行器
global_model = None
executor = ThreadPoolExecutor(max_workers=2)

class ParseRequest(BaseModel):
    task: Optional[str] = None  # 'text', 'formula', 'table' 或 None
    output_format: Optional[str] = "json"  # 'json', 'files'

class ParseResponse(BaseModel):
    status: str
    message: str
    result_path: Optional[str] = None
    processing_time: float
    files_generated: Optional[List[str]] = None
    performance_summary: Optional[dict] = None
    optimization_suggestions: Optional[List[str]] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理器"""
    # Startup
    logger.info("=== MonkeyOCR 服务启动 ===")
    config_path = os.getenv("MONKEYOCR_CONFIG", "model_configs.yaml")
    gpu_id = int(os.getenv("CUDA_DEVICE", "9"))  # 默认使用GPU9
    
    try:
        initialize_model(config_path, gpu_id)
        
        # 初始化性能监控器（使用实际使用的GPU）
        import torch
        if torch.cuda.is_available():
            actual_device = torch.cuda.current_device()
            global_monitor.update_gpu_id(actual_device)
            logger.info(f"性能监控器设置为GPU {actual_device}")
        
        logger.info("=== 模型初始化完成 ===")
        
    except Exception as e:
        logger.error(f"模型初始化失败: {e}")
        raise
    
    yield
    
    # Shutdown
    logger.info("=== MonkeyOCR 服务关闭 ===")
    global executor
    executor.shutdown(wait=True)
    if dist.is_initialized():
        dist.destroy_process_group()
    logger.info("🔄 应用关闭完成")

# 创建FastAPI应用 - 使用现代的lifespan管理器
app = FastAPI(
    title="MonkeyOCR Document Parser Service",
    description="持久化文档解析服务，支持PDF和图像处理",
    version="1.0.0",
    lifespan=lifespan
)

def initialize_model(config_path: str = "model_configs.yaml", gpu_id: int = 9):
    """初始化模型，只在服务启动时调用一次"""
    global global_model
    
    if global_model is None:
        import torch
        
        # 检查GPU可用性
        if not torch.cuda.is_available():
            logger.warning("CUDA不可用，将使用CPU")
            device = "cpu"
        else:
            # 检查指定的GPU是否存在
            num_gpus = torch.cuda.device_count()
            logger.info(f"系统可用GPU数量: {num_gpus}")
            
            if gpu_id >= num_gpus:
                logger.warning(f"指定的GPU {gpu_id} 不存在，可用GPU: 0-{num_gpus-1}")
                gpu_id = num_gpus - 1  # 使用最后一个GPU
                logger.info(f"自动选择GPU {gpu_id}")
            
            # 设置环境变量，让模型使用指定GPU
            os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_id)
            
            # 重新初始化CUDA上下文
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                # 在设置CUDA_VISIBLE_DEVICES后，GPU索引变为0
                device = "cuda:0"
                torch.cuda.set_device(0)
                logger.info(f"设置GPU设备: 物理GPU{gpu_id} -> 逻辑GPU0")
            else:
                device = "cpu"
                logger.warning("设置CUDA_VISIBLE_DEVICES后CUDA仍不可用，使用CPU")
        
        logger.info("正在加载MonkeyOCR模型...")
        start_time = time.time()
        
        try:
            global_model = MonkeyOCR(config_path)
            
            # 确保模型在正确的GPU上
            if hasattr(global_model, 'device'):
                logger.info(f"模型当前设备: {global_model.device}")
            
            load_time = time.time() - start_time
            logger.info(f"模型加载完成，耗时: {load_time:.2f}秒")
            
            # 显示GPU内存使用情况
            if torch.cuda.is_available():
                current_device = torch.cuda.current_device()
                memory_allocated = torch.cuda.memory_allocated(current_device) / 1024**3
                memory_reserved = torch.cuda.memory_reserved(current_device) / 1024**3
                logger.info(f"GPU{current_device} (物理GPU{gpu_id}) 内存使用: {memory_allocated:.2f}GB / {memory_reserved:.2f}GB")
                
        except Exception as e:
            logger.error(f"模型加载失败: {e}")
            raise
    
    return global_model

def single_task_recognition(file_bytes: bytes, filename: str, output_dir: str, task: str):
    """单任务识别"""
    logger.info(f"开始单任务识别: {task}")
    logger.info(f"处理文件: {filename}")
    
    # 获取全局模型
    model = global_model
    if model is None:
        raise HTTPException(status_code=500, detail="模型未初始化")
    
    # 获取文件名（不含扩展名）
    name_without_suff = '.'.join(filename.split(".")[:-1])
    
    # 准备输出目录
    local_md_dir = os.path.join(output_dir, name_without_suff)
    os.makedirs(local_md_dir, exist_ok=True)
    
    logger.info(f"输出目录: {local_md_dir}")
    md_writer = FileBasedDataWriter(local_md_dir)
    
    # 获取任务指令
    instruction = TASK_INSTRUCTIONS.get(task, TASK_INSTRUCTIONS['text'])
    
    # 检查文件类型并准备图像
    file_extension = filename.split(".")[-1].lower()
    images = []
    
    if file_extension == 'pdf':
        logger.info("检测到PDF文件，转换为图像...")
        try:
            # 将字节数据写入临时文件
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp_pdf:
                temp_pdf.write(file_bytes)
                temp_pdf_path = temp_pdf.name
            
            # 转换PDF页面为PIL图像
            images = convert_from_path(temp_pdf_path, dpi=150)
            logger.info(f"转换了 {len(images)} 页图像")
            
            # 清理临时文件
            os.unlink(temp_pdf_path)
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"PDF转换失败: {str(e)}")
            
    elif file_extension in ['jpg', 'jpeg', 'png']:
        # 加载单个图像
        from PIL import Image
        from io import BytesIO
        images = [Image.open(BytesIO(file_bytes))]
    else:
        raise HTTPException(status_code=400, detail=f"不支持的文件格式: {file_extension}")
    
    # 开始识别
    logger.info(f"对 {len(images)} 张图像执行 {task} 识别...")
    start_time = time.time()
    
    try:
        # 为所有图像准备指令
        instructions = [instruction] * len(images)
        
        # 使用聊天模型进行单任务识别
        responses = model.chat_model.batch_inference(images, instructions)  # type: ignore
        
        recognition_time = time.time() - start_time
        logger.info(f"识别时间: {recognition_time:.2f}秒")
        
        # 合并结果
        combined_result = responses[0]
        for i, response in enumerate(responses):
            if i > 0:
                combined_result = combined_result + "\n\n" + response
        
        # 保存结果
        result_filename = f"{name_without_suff}_{task}_result.md"
        md_writer.write(result_filename, combined_result.encode('utf-8'))
        
        logger.info(f"单任务识别完成!")
        logger.info(f"任务类型: {task}")
        logger.info(f"处理了 {len(images)} 张图像")
        logger.info(f"结果保存到: {os.path.join(local_md_dir, result_filename)}")
        
        return local_md_dir, [result_filename]
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"单任务识别失败: {str(e)}")

def parse_pdf(file_bytes: bytes, filename: str, output_dir: str):
    """解析PDF文件"""
    logger.info(f"开始解析文件: {filename}")
    
    # 重置性能监控器
    global_monitor.reset()
    
    # 获取全局模型
    model = global_model
    if model is None:
        raise HTTPException(status_code=500, detail="模型未初始化")
    
    # 获取文件名（不含扩展名）
    name_without_suff = '.'.join(filename.split(".")[:-1])
    
    with global_monitor.time_phase("预处理-准备输出目录"):
        # 准备输出目录
        local_image_dir = os.path.join(output_dir, name_without_suff, "images")
        local_md_dir = os.path.join(output_dir, name_without_suff)
        image_dir = os.path.basename(local_image_dir)
        os.makedirs(local_image_dir, exist_ok=True)
        os.makedirs(local_md_dir, exist_ok=True)
        
        logger.info(f"输出目录: {local_md_dir}")
        image_writer = FileBasedDataWriter(local_image_dir)
        md_writer = FileBasedDataWriter(local_md_dir)
    
    with global_monitor.time_phase("预处理-创建数据集"):
        # 创建数据集实例
        file_extension = filename.split(".")[-1].lower()
        if file_extension == "pdf":
            ds = PymuDocDataset(file_bytes)
        else:
            ds = ImageDataset(file_bytes)
        
        # 记录页面信息
        global_monitor.log_page_info(len(ds))
    
    # 开始推理
    logger.info("执行文档解析...")
    
    with global_monitor.time_phase("布局检测-文档分析"):
        infer_result = ds.apply(doc_analyze_llm, MonkeyOCR_model=model)
    
    with global_monitor.time_phase("内容识别-OCR处理"):
        # 流水线处理
        pipe_result = infer_result.pipe_ocr_mode(image_writer, MonkeyOCR_model=model)

    with global_monitor.time_phase("后处理-生成输出文件"):
        # 生成输出文件
        generated_files = []
        
        model_pdf_path = os.path.join(local_md_dir, f"{name_without_suff}_model.pdf")
        infer_result.draw_model(model_pdf_path)
        generated_files.append(f"{name_without_suff}_model.pdf")
        
        layout_pdf_path = os.path.join(local_md_dir, f"{name_without_suff}_layout.pdf")
        pipe_result.draw_layout(layout_pdf_path)
        generated_files.append(f"{name_without_suff}_layout.pdf")

        spans_pdf_path = os.path.join(local_md_dir, f"{name_without_suff}_spans.pdf")
        pipe_result.draw_span(spans_pdf_path)
        generated_files.append(f"{name_without_suff}_spans.pdf")

        md_path = f"{name_without_suff}.md"
        pipe_result.dump_md(md_writer, md_path, image_dir)
        generated_files.append(md_path)
        
        content_list_path = f"{name_without_suff}_content_list.json"
        pipe_result.dump_content_list(md_writer, content_list_path, image_dir)
        generated_files.append(content_list_path)

        middle_json_path = f'{name_without_suff}_middle.json'
        pipe_result.dump_middle_json(md_writer, middle_json_path)
        generated_files.append(middle_json_path)
    
    # 打印详细性能报告
    global_monitor.print_detailed_report()
    
    # 获取优化建议
    suggestions = global_monitor.get_optimization_suggestions()
    logger.info("🚀 性能优化建议:")
    for suggestion in suggestions:
        logger.info(suggestion)
    
    # 获取性能摘要
    performance_summary = global_monitor.get_summary()
    
    logger.info(f"结果保存到: {local_md_dir}")
    return local_md_dir, generated_files, performance_summary, suggestions

@app.get("/")
async def root():
    """主页接口"""
    return {
        "service": "MonkeyOCR Document Parser Service",
        "status": "running",
        "version": "1.0.0",
        "model_loaded": global_model is not None,
        "device": global_model.device if global_model and hasattr(global_model, 'device') else "N/A",
        "supported_tasks": list(TASK_INSTRUCTIONS.keys()),
        "supported_formats": ["pdf", "jpg", "jpeg", "png"],
        "description": "持久化文档解析服务，支持PDF和图像处理"
    }

@app.get("/health")
async def health_check():
    """健康检查接口"""
    return {
        "status": "healthy",
        "model_loaded": global_model is not None,
        "device": global_model.device if global_model and hasattr(global_model, 'device') else "N/A",
        "timestamp": time.time(),
        "executor_status": "running" if not executor._shutdown else "shutdown"
    }

@app.post("/parse", response_model=ParseResponse)
async def parse_document(
    file: UploadFile = File(...),
    task: Optional[str] = Form(None),
    output_format: str = Form("json")
):
    """
    解析文档接口
    
    参数:
    - file: 上传的PDF或图像文件
    - task: 可选，单任务类型 ('text', 'formula', 'table')
    - output_format: 输出格式 ('json', 'files')
    """
    
    if global_model is None:
        raise HTTPException(status_code=500, detail="模型未初始化")
    
    # 验证文件类型
    file_extension = file.filename.split(".")[-1].lower()  # type: ignore
    supported_extensions = {'pdf', 'jpg', 'jpeg', 'png'}
    if file_extension not in supported_extensions:
        raise HTTPException(
            status_code=400, 
            detail=f"不支持的文件格式: {file_extension}，支持的格式: {supported_extensions}"
        )
    
    # 验证任务类型
    if task and task not in TASK_INSTRUCTIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的任务类型: {task}，支持的类型: {list(TASK_INSTRUCTIONS.keys())}"
        )
    
    start_time = time.time()
    
    try:
        # 验证文件名
        if not file.filename:
            raise HTTPException(status_code=400, detail="文件名不能为空")
        
        # 读取文件内容
        file_bytes = await file.read()
        
        # 创建输出目录
        backend_output_dir = "/home/zt/pdf2ppt/backend/outputs"
        os.makedirs(backend_output_dir, exist_ok=True)
        timestamp = str(int(time.time()))
        session_id = f"monkeyocr_{timestamp}"
        output_dir = os.path.join(backend_output_dir, session_id)
        os.makedirs(output_dir, exist_ok=True)
        
        try:
            if task:
                # 单任务识别 - 使用异步执行器
                loop = asyncio.get_event_loop()
                result_dir, generated_files = await loop.run_in_executor(
                    executor,
                    single_task_recognition,
                    file_bytes, file.filename, output_dir, task
                )
                # 单任务识别没有性能监控，设置默认值
                performance_summary = {}
                optimization_suggestions = []
            else:
                # 完整PDF解析 - 使用异步执行器
                loop = asyncio.get_event_loop()
                result_dir, generated_files, performance_summary, optimization_suggestions = await loop.run_in_executor(
                    executor,
                    parse_pdf,
                    file_bytes, file.filename, output_dir
                )
            
            processing_time = time.time() - start_time
            
            if output_format == "files":
                # 创建ZIP文件返回
                zip_path = f"{result_dir}.zip"
                with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for root, dirs, files in os.walk(result_dir):
                        for file_name in files:
                            file_path = os.path.join(root, file_name)
                            arc_name = os.path.relpath(file_path, result_dir)
                            zipf.write(file_path, arc_name)
                
                return FileResponse(
                    zip_path,
                    media_type="application/zip",
                    filename=f"{file.filename}_result.zip"
                )
            
            else:
                # 返回JSON响应
                performance_data = performance_summary if 'performance_summary' in locals() else None
                suggestions = optimization_suggestions if 'optimization_suggestions' in locals() else None
                
                return ParseResponse(
                    status="success",
                    message="文档解析完成",
                    result_path=result_dir,
                    processing_time=processing_time,
                    files_generated=generated_files,
                    performance_summary=performance_data,
                    optimization_suggestions=suggestions
                )
        
        finally:
            # 清理临时目录（如果不是返回文件的话）
            if output_format != "files":
                try:
                    shutil.rmtree(output_dir)
                except Exception as e:
                    logger.warning(f"清理临时目录失败: {e}")
    
    except HTTPException:
        raise
    except Exception as e:
        processing_time = time.time() - start_time
        logger.error(f"文档解析失败: {str(e)}")
        return ParseResponse(
            status="error",
            message=f"文档解析失败: {str(e)}",
            processing_time=processing_time
        )

@app.post("/parse_batch")
async def parse_batch_documents(
    files: List[UploadFile] = File(...),
    task: Optional[str] = Form(None)
):
    """
    批量解析文档接口
    """
    if global_model is None:
        raise HTTPException(status_code=500, detail="模型未初始化")
    
    if len(files) > 10:  # 限制批量处理数量
        raise HTTPException(status_code=400, detail="批量处理文件数量不能超过10个")
    
    results = []
    total_start_time = time.time()
    
    for file in files:
        try:
            # 验证文件名
            if not file.filename:
                results.append({
                    "filename": "unknown",
                    "status": "error",
                    "message": "文件名不能为空"
                })
                continue
                
            # 验证文件类型
            file_extension = file.filename.split(".")[-1].lower()
            supported_extensions = {'pdf', 'jpg', 'jpeg', 'png'}
            if file_extension not in supported_extensions:
                results.append({
                    "filename": file.filename,
                    "status": "error",
                    "message": f"不支持的文件格式: {file_extension}"
                })
                continue
            
            # 处理单个文件
            file_bytes = await file.read()
            backend_output_dir = "/home/zt/pdf2ppt/backend/outputs"
            os.makedirs(backend_output_dir, exist_ok=True)
            timestamp = str(int(time.time() * 1000))  # 毫秒级时间戳避免冲突
            session_id = f"monkeyocr_batch_{timestamp}"
            output_dir = os.path.join(backend_output_dir, session_id)
            os.makedirs(output_dir, exist_ok=True)
            
            start_time = time.time()
            
            # 使用异步执行器处理文件
            loop = asyncio.get_event_loop()
            if task:
                result_dir, generated_files = await loop.run_in_executor(
                    executor,
                    single_task_recognition,
                    file_bytes, file.filename, output_dir, task
                )
            else:
                result_dir, generated_files, _, _ = await loop.run_in_executor(
                    executor,
                    parse_pdf,
                    file_bytes, file.filename, output_dir
                )
            
            processing_time = time.time() - start_time
            
            results.append({
                "filename": file.filename,
                "status": "success",
                "result_path": result_dir,
                "processing_time": processing_time,
                "files_generated": generated_files
            })
            
        except Exception as e:
            results.append({
                "filename": file.filename,
                "status": "error",
                "message": str(e),
                "processing_time": time.time() - start_time if 'start_time' in locals() else 0
            })
    
    total_processing_time = time.time() - total_start_time
    
    return {
        "status": "completed",
        "total_files": len(files),
        "successful": len([r for r in results if r["status"] == "success"]),
        "failed": len([r for r in results if r["status"] == "error"]),
        "total_processing_time": total_processing_time,
        "results": results
    }

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="MonkeyOCR持久化服务")
    parser.add_argument("--host", default="0.0.0.0", help="服务监听地址")
    parser.add_argument("--port", type=int, default=8000, help="服务监听端口")
    parser.add_argument("--config", default="model_configs.yaml", help="模型配置文件路径")
    parser.add_argument("--workers", type=int, default=1, help="工作进程数")
    
    args = parser.parse_args()
    
    # 设置环境变量
    os.environ["MONKEYOCR_CONFIG"] = args.config
    
    logger.info(f"启动MonkeyOCR服务...")
    logger.info(f"监听地址: {args.host}:{args.port}")
    logger.info(f"配置文件: {args.config}")
    
    uvicorn.run(
        "parse_server:app",
        host=args.host,
        port=args.port,
        workers=args.workers,
        reload=False
    ) 