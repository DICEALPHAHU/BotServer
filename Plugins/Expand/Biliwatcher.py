"""
B站UP动态监控插件（定制格式版）
适配NoneBot2 + OneBot v11
核心：仅推送「本群订阅UP主+昵称+截图+链接」，移除多余提示
"""
import asyncio
import re
import os
import sys
import cv2
import numpy as np
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from bilibili_api import user, video
from bilibili_api.exceptions import ApiException, NetworkException
from bilibili_api.video import (
    VideoStreamDownloadURL,
    VideoQuality,
    VideoCodecs,
    VideoDownloadURLDataDetecter
)

# ====================== 核心配置 ======================
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

# NoneBot导入
from nonebot import logger, on_command, get_bot, get_driver
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.permission import SUPERUSER

# 加载.env
from dotenv import load_dotenv
env_path = PROJECT_ROOT / ".env"
if not env_path.exists():
    raise FileNotFoundError(f".env文件不存在！路径：{env_path.absolute()}")
load_dotenv(env_path, encoding="utf-8", override=True)

# 配置验证
BILI_WATCHER_ENABLED = os.getenv("BILI_WATCHER_ENABLED", "false").lower() == "true"
BILI_UP_UID = os.getenv("BILI_UP_UID", "").strip()
BILI_UP_WAITSEC = int(os.getenv("BILI_UP_WAITSEC", 30))  # 默认30秒
MESSAGE_GROUPS = [str(g) for g in eval(os.getenv("MESSAGE_GROUPS", "[]"))]

if BILI_WATCHER_ENABLED:
    if not BILI_UP_UID or not BILI_UP_UID.isdigit():
        raise ValueError(".env中BILI_UP_UID必须为非空数字！")
    if BILI_UP_WAITSEC < 10:
        BILI_UP_WAITSEC = 10
        logger.warning("⚠️ 监控间隔小于10秒，强制改为10秒")
    if not MESSAGE_GROUPS:
        raise ValueError(".env中MESSAGE_GROUPS不能为空！")

# ====================== 插件元信息 ======================
__plugin_meta__ = PluginMetadata(
    name="B站UP动态监控（定制格式版）",
    description="仅推送「本群订阅UP主+昵称+截图+链接」，无多余提示",
    usage="""
    .bilitestv <B站视频链接> - 测试定制格式推送
    .bilicheckuid - 验证UP主UID
    .biliclear - 清理缓存
    """,
)

# ====================== 路径配置 ======================
TEMP_ROOT = PROJECT_ROOT / "Temp" / "BILI"
VIDEO_DIR = TEMP_ROOT / "videos"
FRAME_DIR = TEMP_ROOT / "frames"
for dir_path in [VIDEO_DIR, FRAME_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)

# ====================== 全局变量 ======================
MONITOR_LIST: Dict[str, Dict] = {}
PROCESSED_DYNAMICS: List[str] = []
PROCESSED_MAX_LEN = 200
DRIVER = get_driver()
UP_NAME_CACHE = ""  # 缓存UP主昵称
BV_PATTERN = re.compile(r"BV\w+")

# ====================== 核心工具函数 ======================
async def get_up_info_async(uid: str) -> Optional[Dict]:
    """获取UP主信息（缓存昵称）"""
    global UP_NAME_CACHE
    try:
        u = user.User(uid=int(uid))
        up_info = await u.get_user_info()
        if up_info and "name" in up_info:
            UP_NAME_CACHE = up_info["name"]  # 缓存昵称
        return up_info
    except ApiException as e:
        logger.error(f"API错误：{e.code} - {e.message}")
    except NetworkException as e:
        logger.error(f"网络错误：状态码 {e.status_code if hasattr(e, 'status_code') else '未知'} - {str(e)}")
    except Exception as e:
        logger.error(f"获取UP{uid}信息异常：{str(e)}")
    return None

async def get_up_dynamics_async(uid: str, offset: str = "") -> Optional[Dict]:
    """获取UP主动态"""
    try:
        u = user.User(uid=int(uid))
        try:
            dynamics = await u.get_dynamics_new(offset=offset)
        except:
            dynamics = await u.get_dynamics()
            dynamics = {"items": dynamics, "offset": ""} if isinstance(dynamics, list) else dynamics
        return dynamics
    except ApiException as e:
        logger.error(f"API错误：{e.code} - {e.message}")
    except NetworkException as e:
        logger.error(f"网络错误：状态码 {e.status_code if hasattr(e, 'status_code') else '未知'} - {str(e)}")
    except Exception as e:
        logger.error(f"获取UP{uid}动态异常：{str(e)}")
    return None

async def get_video_stream_by_VSDU_async(video_url: str) -> Optional[Path]:
    """仅用VideoStreamDownloadURL获取MP4视频流"""
    bv_id = None
    try:
        bv_match = BV_PATTERN.search(video_url)
        if not bv_match:
            return None
        bv_id = bv_match.group()
        video_path = VIDEO_DIR / f"{bv_id}.mp4"

        v = video.Video(bvid=bv_id)
        video_info = await v.get_info()
        cid = video_info["pages"][0]["cid"]

        # 核心VSDU流解析
        download_data = await v.get_download_url(cid=cid, html5=True)
        detecter = VideoDownloadURLDataDetecter(download_data)
        best_streams = detecter.detect_best_streams(
            video_max_quality=VideoQuality._720P,
            video_min_quality=VideoQuality._480P,
            codecs=[VideoCodecs.AVC]
        )

        if not best_streams or not hasattr(best_streams[0], "url"):
            return None
        
        stream_url = best_streams[0].url
        if not stream_url.startswith(("http://", "https://")):
            stream_url = f"https:{stream_url}"

        # 下载视频流
        import requests
        requests.packages.urllib3.disable_warnings()
        resp = requests.get(
            stream_url,
            timeout=120,
            stream=True,
            verify=False,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
                "Referer": "https://www.bilibili.com/"
            }
        )
        resp.raise_for_status()

        with open(video_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        return video_path

    except Exception:
        return None

def capture_video_frame_fix_color(video_path: Path, save_path: Path) -> Optional[Path]:
    """色彩修复版截帧"""
    try:
        cap = cv2.VideoCapture(str(video_path), cv2.CAP_FFMPEG)
        if not cap.isOpened():
            return None

        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_positions = [int(fps*2), int(fps*1), 0]
        frame_positions = [pos for pos in frame_positions if pos < total_frames]
        if not frame_positions:
            frame_positions = [0]

        frame = None
        for pos in frame_positions:
            cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
            ret, frame = cap.read()
            if ret and frame is not None and frame.size > 0:
                break

        if frame is None or frame.size == 0:
            cap.release()
            return None

        # 直接保存BGR格式，修复色彩
        cv2.imwrite(str(save_path), frame, [cv2.IMWRITE_PNG_COMPRESSION, 5])
        cap.release()
        return save_path

    except Exception:
        return None

async def get_video_frame_by_VSDU_async(video_url: str) -> Optional[Path]:
    """一站式：VSDU获取视频+色彩修复截帧"""
    video_path = await get_video_stream_by_VSDU_async(video_url)
    if not video_path:
        return None

    bv_id = BV_PATTERN.search(video_url).group()
    frame_path = FRAME_DIR / f"{bv_id}.png"
    frame_result = capture_video_frame_fix_color(video_path, frame_path)

    # 清理临时视频
    if video_path.exists():
        video_path.unlink(missing_ok=True)

    return frame_result

# ====================== 核心监控逻辑（定制推送格式） ======================
async def monitor_up_dynamics():
    """监控UP主动态，仅推送定制格式"""
    if not BILI_WATCHER_ENABLED:
        return

    # 初始化UP信息（缓存昵称）
    up_info = None
    while not up_info:
        up_info = await get_up_info_async(BILI_UP_UID)
        if not up_info:
            await asyncio.sleep(10)
    
    logger.success(f"监控启动：每{BILI_UP_WAITSEC}秒检测UP主【{UP_NAME_CACHE}】（UID：{BILI_UP_UID}）")

    while True:
        try:
            # 获取最新动态
            dynamics_data = await get_up_dynamics_async(BILI_UP_UID, MONITOR_LIST.get(BILI_UP_UID, {}).get("last_offset", ""))
            if not dynamics_data or "items" not in dynamics_data:
                await asyncio.sleep(BILI_UP_WAITSEC)
                continue

            # 处理新动态
            for dynamic in dynamics_data["items"]:
                dynamic_id = str(dynamic.get("id_str", dynamic.get("id", "")))
                
                # 跳过已处理/非视频动态
                if dynamic_id in PROCESSED_DYNAMICS or dynamic.get("type") not in ["DYNAMIC_TYPE_AV", "AV"]:
                    if dynamic_id not in PROCESSED_DYNAMICS:
                        PROCESSED_DYNAMICS.append(dynamic_id)
                    continue

                # 提取视频链接
                video_info = dynamic.get("modules", {}).get("module_dynamic", {}).get("major", {}).get("archive", {})
                bvid = video_info.get("bvid", "")
                video_url = f"https://www.bilibili.com/video/{bvid}/" if bvid else ""
                
                if not video_url:
                    PROCESSED_DYNAMICS.append(dynamic_id)
                    continue

                # 获取视频帧
                frame_path = await get_video_frame_by_VSDU_async(video_url)

                # ========== 定制推送格式（核心） ==========
                msg = Message()
                # 仅保留：本群订阅UP主【昵称】的最新动态
                msg += MessageSegment.text(f"本群订阅UP主【{UP_NAME_CACHE}】的最新动态\n")
                # 有帧则加截图，无则跳过
                if frame_path:
                    msg += MessageSegment.image(f"file:///{frame_path.absolute()}")
                    msg += MessageSegment.text("\n")
                # 仅保留视频链接
                msg += MessageSegment.text(f"链接：{video_url}")

                # 推送至所有指定群
                bot = get_bot()
                for group_id in MESSAGE_GROUPS:
                    try:
                        await bot.send_group_msg(group_id=group_id, message=msg)
                        logger.info(f"推送至群{group_id}：{video_url}")
                    except Exception as e:
                        logger.error(f"推送群{group_id}失败：{str(e)}")

                # 标记已处理
                PROCESSED_DYNAMICS.append(dynamic_id)
                if len(PROCESSED_DYNAMICS) > PROCESSED_MAX_LEN:
                    PROCESSED_DYNAMICS.pop(0)

            # 更新偏移量
            MONITOR_LIST[BILI_UP_UID] = {"last_offset": dynamics_data.get("offset", "")}

        except Exception as e:
            logger.error(f"监控异常：{str(e)}")
        
        await asyncio.sleep(BILI_UP_WAITSEC)

# ====================== 指令处理器（定制格式） ======================
# 测试定制格式推送（仅返回最终格式，无多余提示）
test_vsdu = on_command("bilitestv", permission=SUPERUSER, block=True)
@test_vsdu.handle()
async def handle_test_vsdu(args: Message = CommandArg()):
    if not BILI_WATCHER_ENABLED:
        await test_vsdu.finish("插件已禁用！")
    
    video_url = args.extract_plain_text().strip()
    if not video_url or "bilibili.com" not in video_url:
        await test_vsdu.finish("请输入有效的B站视频链接！")
    
    # 获取帧+组装定制格式
    frame_path = await get_video_frame_by_VSDU_async(video_url)
    msg = Message()
    msg += MessageSegment.text(f"本群订阅UP主【{UP_NAME_CACHE}】的最新动态\n")
    if frame_path:
        msg += MessageSegment.image(f"file:///{frame_path.absolute()}")
        msg += MessageSegment.text("\n")
    msg += MessageSegment.text(f"链接：{video_url}")
    
    await test_vsdu.finish(msg)

# 验证UID（仅返回核心信息）
check_uid = on_command("bilicheckuid", permission=SUPERUSER, block=True)
@check_uid.handle()
async def handle_check_uid():
    up_info = await get_up_info_async(BILI_UP_UID)
    if up_info:
        await check_uid.finish(f"UP主信息：\nUID：{BILI_UP_UID}\n昵称：{UP_NAME_CACHE}")
    else:
        await check_uid.finish(f"无法获取UP主信息（UID：{BILI_UP_UID}）")

# 清理缓存（简化提示）
clean_cache = on_command("biliclear", permission=SUPERUSER, block=True)
@clean_cache.handle()
async def handle_clean_cache():
    try:
        for file in VIDEO_DIR.glob("*.*"):
            file.unlink(missing_ok=True)
        for file in FRAME_DIR.glob("*.*"):
            file.unlink(missing_ok=True)
        await clean_cache.finish("缓存清理完成！")
    except Exception as e:
        await clean_cache.finish(f"清理失败：{str(e)}")

# ====================== 插件启动/关闭 ======================
@DRIVER.on_startup
async def startup():
    if BILI_WATCHER_ENABLED:
        asyncio.create_task(monitor_up_dynamics())

@DRIVER.on_shutdown
async def shutdown():
    logger.success("监控已停止")