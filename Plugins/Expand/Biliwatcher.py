"""
B站UP动态监控插件
吐槽：写这玩意要死，最开始是412反盗链，之后是404，要疯了
这里感谢Nemo2011及其团队的bilibili-api项目大力支持，仓库地址：https://github.com/Nemo2011/bilibili-api
不然我自己去写视频爬虫功能会被CR的412反盗链搞死（现在好像是阿姨掌权了？）。
（你所热爱的就是你的生活——CR柠檬什么时候熟啊！）
更新：我放弃了自动更新逻辑，但石山代码没法改，就保留一个解析视频快照的功能吧，想要自动化动态功能去看这个项目吧，我不想搞了
https://github.com/Starlwr/StarBot
"""
import asyncio
import re
import os
import sys
import cv2
import numpy as np
import aiohttp
from urllib.parse import unquote, urlparse, parse_qs
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
BILI_UP_UID = os.getenv("BILI_UP_UID", "0").strip() # 本区域已经弃用，但是是石山代码，不要动
BILI_UP_WAITSEC = int(os.getenv("BILI_UP_WAITSEC", 3600))  # 本区域已经弃用，但是是石山代码，不要动
MESSAGE_GROUPS = [str(g) for g in eval(os.getenv("MESSAGE_GROUPS", "[]"))]

# ====================== 插件元信息 ======================
__plugin_meta__ = PluginMetadata(
    name="B站UP动态监控",
    description="对特定UP进行动态追踪，获取第一消息",
    usage="""
    .bv <B站视频链接> - 解析B站视频（支持b23.tv短链接）并返回截图
    .bilicheckuid - 验证UP主UID（已经废弃，请勿使用）
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
BV_PATTERN = re.compile(r"BV[a-zA-Z0-9]+")  # 优化BV号匹配规则

# ====================== 新增核心1：B站短链接转长链接 ======================
async def b23_to_long_url(short_url: str) -> str:
    """
    第一步：解析b23.tv短链接为bilibili.com长链接
    :param short_url: 输入的b23.tv短链接
    :return: 转换后的长链接，失败则返回原链接
    """
    if not short_url.startswith(("http://", "https://")):
        short_url = f"https://{short_url}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                short_url,
                timeout=aiohttp.ClientTimeout(10),
                allow_redirects=False,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                }
            ) as resp:
                if resp.status in [301, 302] and "Location" in resp.headers:
                    long_url = unquote(resp.headers["Location"])
                    if long_url.startswith("/"):
                        long_url = f"https://www.bilibili.com{long_url}"
                    logger.info(f"短链接转换成功：{short_url} → {long_url}")
                    return long_url
                else:
                    return short_url
    except Exception as e:
        logger.error(f"短链接转换失败：{e}")
        return short_url

# ====================== 新增核心2：精简长链接（去掉所有参数） ======================
def simplify_bilibili_url(full_url: str) -> str:
    """
    第二步：精简B站长链接，只保留 https://www.bilibili.com/video/BVxxxxxxx 部分
    :param full_url: 完整的B站长链接（带参数）
    :return: 精简后的纯BV号链接
    """
    # 提取BV号
    bv_match = BV_PATTERN.search(full_url)
    if not bv_match:
        return full_url  # 无BV号则返回原链接
    
    bv_id = bv_match.group()
    # 生成精简链接
    simplified_url = f"https://www.bilibili.com/video/{bv_id}/"
    logger.info(f"链接精简成功：{full_url[:50]}... → {simplified_url}")
    return simplified_url

# ====================== 原有核心：视频流下载 + 截图生成 ======================
async def get_video_stream_by_VSDU_async(video_url: str) -> Optional[Path]:
    """根据长链接下载视频流"""
    bv_match = BV_PATTERN.search(video_url)
    if not bv_match:
        return None
    bv_id = bv_match.group()
    video_path = VIDEO_DIR / f"{bv_id}.mp4"

    try:
        v = video.Video(bvid=bv_id)
        video_info = await v.get_info()
        cid = video_info["pages"][0]["cid"]

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
    """截取视频帧并修复色彩"""
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

        cv2.imwrite(str(save_path), frame, [cv2.IMWRITE_PNG_COMPRESSION, 5])
        cap.release()
        return save_path

    except Exception:
        return None

async def get_video_frame_by_VSDU_async(video_url: str) -> Optional[Path]:
    """一站式：视频流下载 + 截图生成"""
    video_path = await get_video_stream_by_VSDU_async(video_url)
    if not video_path:
        return None

    bv_id = BV_PATTERN.search(video_url).group()
    frame_path = FRAME_DIR / f"{bv_id}.png"
    frame_result = capture_video_frame_fix_color(video_path, frame_path)

    if video_path.exists():
        video_path.unlink(missing_ok=True)

    return frame_result

# ====================== 指令入口：串联所有步骤 ======================
test_vsdu = on_command("bv", permission=SUPERUSER, block=True)
@test_vsdu.handle()
async def handle_test_vsdu(args: Message = CommandArg()):
    if not BILI_WATCHER_ENABLED:
        await test_vsdu.finish("插件已禁用！")
    
    # 1. 提取输入链接
    input_url = args.extract_plain_text().strip()
    
    # 2. 校验链接有效性
    if not input_url or ("bilibili.com" not in input_url and "b23.tv" not in input_url):
        await test_vsdu.finish("请输入有效的B站视频链接（支持b23.tv短链接）！")
    
    # 3. 步骤1：短链接转长链接
    long_url = input_url
    if "b23.tv" in input_url:
        long_url = await b23_to_long_url(input_url)
    
    # 4. 步骤2：精简长链接（核心修改：去掉所有参数）
    final_url = simplify_bilibili_url(long_url)
    
    # 5. 步骤3：解析视频截图（用精简后的链接不影响截图生成）
    frame_path = await get_video_frame_by_VSDU_async(final_url)
    
    # 6. 返回结果（只显示精简后的链接）
    msg = Message()
    msg += MessageSegment.text(f"视频解析结果\n")
    if frame_path:
        msg += MessageSegment.image(f"file:///{frame_path.absolute()}")
        msg += MessageSegment.text("\n")
    msg += MessageSegment.text(f"链接：{final_url}")
    
    await test_vsdu.finish(msg)

# ====================== 保留原有废弃指令 ======================
check_uid = on_command("bilicheckuid", permission=SUPERUSER, block=True)
@check_uid.handle()
async def handle_check_uid():
    up_info = await get_up_info_async(BILI_UP_UID)
    if up_info:
        await check_uid.finish(f"UP主信息：\nUID：{BILI_UP_UID}\n昵称：{UP_NAME_CACHE}")
    else:
        await check_uid.finish(f"无法获取UP主信息（UID：{BILI_UP_UID}）")

# 清理缓存指令
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

# 补全原文件缺失的函数
async def get_up_info_async(uid: str) -> Optional[Dict]:
    """获取UP主信息（缓存昵称）"""
    global UP_NAME_CACHE
    try:
        u = user.User(uid=int(uid))
        up_info = await u.get_user_info()
        if up_info and "name" in up_info:
            UP_NAME_CACHE = up_info["name"]
        return up_info
    except ApiException as e:
        logger.error(f"API错误：{e.code} - {e.message}")
    except NetworkException as e:
        logger.error(f"网络错误：{e.status_code if hasattr(e, 'status_code') else '未知'} - {str(e)}")
    except Exception as e:
        logger.error(f"获取UP{uid}信息异常：{str(e)}")
    return None
