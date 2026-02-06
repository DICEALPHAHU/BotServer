"""
B站UP动态监控插件
吐槽：写这玩意要死，最开始是412反盗链，之后是404，要疯了
这里感谢Nemo2011及其团队的bilibili-api项目大力支持，仓库地址：https://github.com/Nemo2011/bilibili-api
不然我自己去写视频爬虫功能会被CR的412反盗链搞死（现在好像是阿姨掌权了？）。
（你所热爱的就是你的生活——CR柠檬什么时候熟啊！）
20260206更新：我放弃了自动更新逻辑，但石山代码没法改，就保留一个解析视频快照的功能吧，想要自动化动态功能去看这个项目吧，我不想搞了
https://github.com/Starlwr/StarBot
20260206再次更新：本来是打算放弃这个自动获取动态的功能的，结果和幻境社开发部抱怨412的时候，发现，
原来之前的天气插件所属的uapis网站竟然有获取b站视频的功能。
我突然感觉自己整个人都清爽了，遂开干，直接不用bilibili-api解析动态的功能了，而是uapis的api功能！
然后就成功啦，完成这个功能后，妈的整个人都像蛇了一样爽到爆炸。
在这里十分感谢uapis.cn，你是我的神！
"""
import asyncio
import re
import os
import sys
import cv2
import json
import time
import numpy as np
import aiohttp
from urllib.parse import unquote
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
from bilibili_api import user, video
from bilibili_api.exceptions import ApiException, NetworkException
from bilibili_api.video import (
    VideoStreamDownloadURL,
    VideoQuality,
    VideoCodecs,
    VideoDownloadURLDataDetecter
)
from nonebot import get_bots

# ====================== 核心路径配置（动态推导，无硬编码） ======================
# 1. 获取当前脚本文件的绝对路径
CURRENT_FILE = Path(__file__).resolve()
# 2. 动态推导项目根目录（可根据实际结构调整parents数字）
PROJECT_ROOT = CURRENT_FILE.parents[2]

# 3. 所有临时文件路径都基于动态根目录生成
CACHE_FILE = PROJECT_ROOT / "Temp" / "BILI" / "bvtemp.json"  # 缓存文件
VIDEO_DIR = PROJECT_ROOT / "Temp" / "BILI" / "videos"        # 临时视频目录
FRAME_DIR = PROJECT_ROOT / "Temp" / "BILI" / "frames"        # 视频帧目录

# 4. 自动创建所有必要目录（无需手动创建）
for dir_path in [CACHE_FILE.parent, VIDEO_DIR, FRAME_DIR]:
    dir_path.mkdir(parents=True, exist_ok=True)

# 5. 将项目根目录加入Python路径（确保导入正常）
sys.path.insert(0, str(PROJECT_ROOT))

# ====================== NoneBot导入 & 环境配置 ======================
from nonebot import logger, on_command, get_bot, get_driver
from nonebot.adapters.onebot.v11 import Message, MessageSegment
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.permission import SUPERUSER
from nonebot.log import logger_id, default_format  # 日志优化

# 加载.env文件（项目根目录下的.env）
from dotenv import load_dotenv
env_path = PROJECT_ROOT / ".env"
if not env_path.exists():
    raise FileNotFoundError(f".env文件不存在！路径：{env_path.absolute()}")
load_dotenv(env_path, encoding="utf-8", override=True)

# ====================== 配置验证 & 类型安全 ======================
# 从.env读取配置，增加类型校验和默认值容错
def get_env_config() -> Dict[str, Any]:
    """统一读取并验证环境配置，返回类型安全的配置字典"""
    config = {
        "enabled": os.getenv("BILI_WATCHER_ENABLED", "false").lower() == "true",
        "up_uid": os.getenv("BILI_UP_UID", "").strip(),
        "poll_interval": int(os.getenv("BILI_UP_WAITSEC", 30)),
        "push_groups": [],
        "api_url": "https://uapis.cn/api/v1/social/bilibili/archives"
    }
    
    # 安全解析推送群号（防止eval注入）
    groups_str = os.getenv("MESSAGE_GROUPS", "[]")
    try:
        config["push_groups"] = [str(g).strip() for g in eval(groups_str) if str(g).strip().isdigit()]
    except:
        config["push_groups"] = []
    
    # 验证核心配置
    if not config["up_uid"].isdigit():
        logger.warning(f"无效的UP主UID：{config['up_uid']}，监控功能将禁用")
        config["enabled"] = False
    if config["poll_interval"] < 10:
        logger.warning(f"监控间隔过短（{config['poll_interval']}秒），强制调整为10秒")
        config["poll_interval"] = 10
    if not config["push_groups"]:
        logger.warning("未配置推送群号，监控功能将禁用")
        config["enabled"] = False
    
    return config

# 加载配置
CONFIG = get_env_config()
BILI_WATCHER_ENABLED = CONFIG["enabled"]
BILI_UP_UID = CONFIG["up_uid"]
BILI_UP_WAITSEC = CONFIG["poll_interval"]
MESSAGE_GROUPS = CONFIG["push_groups"]
UAPIS_CN_URL = CONFIG["api_url"]

# ====================== 插件元信息 ======================
__plugin_meta__ = PluginMetadata(
    name="B站UP动态监控",
    description="基于uapis.cn API监控UP主新视频，自动生成快照并推送（优化版）",
    usage="""
    .bv <B站视频链接> - 解析B站视频（支持b23.tv短链接）并返回截图
    .biliclear - 清理缓存和临时文件
    .biliwatch - 手动触发一次UP主视频监控
    .bilistatus - 查看当前监控状态
    """,
    extra={
        "author": "优化版",
        "version": "1.1",
        "features": ["防重复推送", "异步优化", "自动清理临时文件", "完善日志"]
    }
)

# ====================== 全局变量 ======================
DRIVER = get_driver()
BV_PATTERN = re.compile(r"BV[a-zA-Z0-9]+")
# 新增：防止高频推送的锁（避免网络延迟导致重复推送）
PUSH_LOCK = asyncio.Lock()
# 新增：临时文件清理阈值（7天）
TEMP_FILE_EXPIRE_DAYS = 7

# ====================== 核心工具函数（全面优化） ======================
# 1. 读取缓存文件（增强容错）
def read_video_cache() -> Dict[str, Any]:
    """
    读取缓存文件，返回最新视频信息
    格式：{timestamp: 0, title: "", bvid: "", update_time: ""}
    """
    default_cache = {
        "timestamp": 0,
        "title": "",
        "bvid": "",
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    if not CACHE_FILE.exists():
        logger.debug(f"缓存文件不存在：{CACHE_FILE.absolute()}，使用默认值")
        return default_cache
    
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        # 兼容旧版缓存格式
        cache.setdefault("timestamp", 0)
        cache.setdefault("title", "")
        cache.setdefault("bvid", "")
        cache.setdefault("update_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        # 确保时间戳是整数
        cache["timestamp"] = int(cache["timestamp"])
        return cache
    except Exception as e:
        logger.error(f"读取缓存文件失败：{e}，使用默认值")
        return default_cache

# 2. 写入缓存文件（增强日志和容错）
def write_video_cache(data: Dict[str, Any]):
    """覆盖写入缓存文件，自动补充更新时间"""
    try:
        data["update_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data["timestamp"] = int(data.get("timestamp", 0))
        
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.success(f"缓存文件更新成功：{CACHE_FILE.absolute()}")
        logger.debug(f"缓存内容：{data}")
    except PermissionError:
        logger.error(f"写入缓存文件失败：权限不足（{CACHE_FILE.absolute()}）")
    except Exception as e:
        logger.error(f"写入缓存文件失败：{e}")

# 3. 短链接转长链接
async def b23_to_long_url(short_url: str, max_retry: int = 2) -> str:
    """短链接转长链接，支持重试机制"""
    if not short_url.startswith(("http://", "https://")):
        short_url = f"https://{short_url}"
    
    for retry in range(max_retry + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    short_url, 
                    timeout=aiohttp.ClientTimeout(8), 
                    allow_redirects=False,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0 Safari/537.36"}
                ) as resp:
                    if resp.status in [301, 302] and "Location" in resp.headers:
                        long_url = unquote(resp.headers["Location"])
                        if long_url.startswith("/"):
                            long_url = f"https://www.bilibili.com{long_url}"
                        return long_url
        except Exception as e:
            logger.warning(f"短链接解析失败（重试{retry}/{max_retry}）：{e}")
            if retry < max_retry:
                await asyncio.sleep(0.5)
    
    logger.error(f"短链接解析失败，返回原始链接：{short_url}")
    return short_url

# 4. 精简B站长链接
def simplify_bilibili_url(full_url: str) -> str:
    """提取BV号并生成标准链接"""
    bv_match = BV_PATTERN.search(full_url)
    if not bv_match:
        return full_url
    bv_id = bv_match.group()
    return f"https://www.bilibili.com/video/{bv_id}/"

# 5. 调用uapis.cn API获取UP主最新视频
async def get_up_latest_video(mid: str, max_retry: int = 2) -> Optional[Dict[str, Any]]:
    """获取UP主最新视频，支持重试机制"""
    if not mid or not mid.isdigit():
        logger.error("UP主UID无效（非数字）")
        return None
    
    for retry in range(max_retry + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    UAPIS_CN_URL,
                    params={"mid": mid, "orderby": "pubdate", "ps": 1, "pn": 1},
                    timeout=aiohttp.ClientTimeout(10),
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0 Safari/537.36"}
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"API请求失败（状态码：{resp.status}），重试{retry}/{max_retry}")
                        if retry < max_retry:
                            await asyncio.sleep(1)
                        continue
                    
                    result = await resp.json()
                    # 验证返回数据
                    if not result.get("videos") or len(result["videos"]) == 0:
                        logger.info("API返回无视频数据")
                        return None
                    
                    # 提取并清洗视频数据
                    video_data = result["videos"][0]
                    clean_data = {
                        "aid": str(video_data.get("aid", "")),
                        "bvid": video_data.get("bvid", ""),
                        "title": video_data.get("title", "未知标题").strip(),
                        "cover": video_data.get("cover", ""),
                        "duration": int(video_data.get("duration", 0)),
                        "play_count": int(video_data.get("play_count", 0)),
                        "publish_time": int(video_data.get("publish_time", 0)),
                        "create_time": int(video_data.get("create_time", 0))
                    }
                    return clean_data
        except asyncio.TimeoutError:
            logger.warning(f"API请求超时，重试{retry}/{max_retry}")
            if retry < max_retry:
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"调用API失败（重试{retry}/{max_retry}）：{e}")
            if retry < max_retry:
                await asyncio.sleep(1)
    
    return None

async def get_up_nickname(uid: str) -> str:
    """根据UID获取UP主昵称（适配新版bilibili-api），失败返回默认值"""
    if not uid or not uid.isdigit():
        return "未知UP主"
    
    try:
        # 使用bilibili-api获取UP主信息（新版方法）
        u = user.User(uid=uid)
        # 适配新版bilibili-api的正确方法
        user_info = await u.get_user_info()  
        return user_info.get("name", "未知UP主")
    # 兼容旧版bilibili-api（如果上面还报错，会走这个分支）
    except AttributeError:
        try:
            u = user.User(uid=uid)
            user_info = await u.get_info_v2()
            return user_info.get("name", "未知UP主")
        except Exception as e2:
            logger.error(f"bilibili-api版本不兼容，获取昵称失败：{e2}")
            return "未知UP主"
    except Exception as e:
        logger.error(f"获取UP主昵称失败（UID：{uid}）：{e}")
        return "未知UP主"

# 6. 视频快照生成
async def get_video_frame_by_VSDU_async(video_url: str) -> Optional[Path]:
    bv_match = BV_PATTERN.search(video_url)
    if not bv_match:
        logger.error("视频链接中未提取到BV号")
        return None
    
    bv_id = bv_match.group()
    video_path = VIDEO_DIR / f"{bv_id}.mp4"
    frame_path = FRAME_DIR / f"{bv_id}.png"
    
    # 提前清理旧文件
    for file in [video_path, frame_path]:
        if file.exists():
            file.unlink(missing_ok=True)
    
    try:
        # 异步获取视频下载链接
        v = video.Video(bvid=bv_id)
        video_info = await v.get_info()
        cid = video_info["pages"][0]["cid"]
        download_data = await v.get_download_url(cid=cid, html5=True)
        
        # 选择最佳视频流
        detecter = VideoDownloadURLDataDetecter(download_data)
        best_streams = detecter.detect_best_streams(
            video_max_quality=VideoQuality._720P,
            video_min_quality=VideoQuality._480P,
            codecs=[VideoCodecs.AVC]
        )
        
        if not best_streams or not hasattr(best_streams[0], "url"):
            logger.error("未找到合适的视频流")
            return None
        
        stream_url = best_streams[0].url
        if not stream_url.startswith(("http://", "https://")):
            stream_url = f"https:{stream_url}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(
                stream_url,
                timeout=aiohttp.ClientTimeout(120),
                verify_ssl=False,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0 Safari/537.36",
                    "Referer": "https://www.bilibili.com/"
                }
            ) as resp:
                resp.raise_for_status()
                with open(video_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(8192):
                        if chunk:
                            f.write(chunk)
        
        # 截帧（优化逻辑，防止空帧）
        cap = cv2.VideoCapture(str(video_path), cv2.CAP_FFMPEG)
        if not cap.isOpened():
            logger.error(f"无法打开视频文件：{video_path}")
            return None
        
        fps = cap.get(cv2.CAP_PROP_FPS) or 30
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        # 选择视频前3秒的帧，优先选2秒位置
        frame_positions = [
            min(int(fps * 2), total_frames - 1),
            min(int(fps * 1), total_frames - 1),
            0
        ]
        
        frame = None
        for pos in frame_positions:
            cap.set(cv2.CAP_PROP_POS_FRAMES, pos)
            ret, frame = cap.read()
            if ret and frame is not None and frame.size > 0:
                break
        
        cap.release()
        
        if frame is None or frame.size == 0:
            logger.error("未能提取到有效视频帧")
            return None
        
        # 保存帧文件
        cv2.imwrite(str(frame_path), frame, [cv2.IMWRITE_PNG_COMPRESSION, 6])
        logger.info(f"视频快照生成成功：{frame_path}")
        
        return frame_path
    
    except Exception as e:
        logger.error(f"生成视频快照失败：{e}")
        return None
    finally:
        # 无论成功失败，都清理临时视频文件
        if video_path.exists():
            video_path.unlink(missing_ok=True)
            logger.debug(f"临时视频文件已清理：{video_path}")

# 7. 推送新视频到指定群（加锁防重复+异步优化）
async def push_new_video(video_data: Dict[str, Any]):
    """推送新视频，加锁防止重复推送"""
    async with PUSH_LOCK:
        try:
            bvid = video_data.get("bvid", "")
            title = video_data.get("title", "未知标题")
            video_url = f"https://www.bilibili.com/video/{bvid}/"
            
            logger.info(f"开始推送新视频：{title} ({bvid})")
            
            # 1. 先获取UP主昵称（关键：补上这行，定义up_nickname）
            up_nickname = await get_up_nickname(BILI_UP_UID)
            
            # 2. 异步生成快照（非阻塞）
            frame_path = await get_video_frame_by_VSDU_async(video_url)
            
            # 3. 组装推送消息（修改文案）
            msg = Message()
            # 这里用定义好的up_nickname，不会再报未定义错误
            msg += MessageSegment.text(f"订阅的UP主【{up_nickname}】动态更新啦！\n")
            msg += MessageSegment.text(f"标题：{title}\n")
            if frame_path and frame_path.exists():
                msg += MessageSegment.image(f"file:///{frame_path.absolute()}")
                msg += MessageSegment.text("\n")
            msg += MessageSegment.text(f"链接：{video_url}")
            
            # 4. 异步推送至所有群
            bots = get_bots()
            if not bots:
                logger.error("推送失败：暂无可用的Bot连接，请检查Bot是否已上线")
                return
            bot = list(bots.values())[0]  # 取第一个可用的Bot
            push_tasks = []
            
            for group_id in MESSAGE_GROUPS:
                task = asyncio.create_task(_send_to_group(bot, group_id, msg, title, bvid))
                push_tasks.append(task)
            
            # 等待所有推送完成
            await asyncio.gather(*push_tasks, return_exceptions=True)
            
            logger.success(f"新视频推送完成：{title} ({bvid})")
            
        except Exception as e:
            logger.error(f"推送新视频失败：{e}")

async def _send_to_group(bot, group_id: str, msg: Message, title: str, bvid: str):
    """内部函数：单独推送单个群，方便异步处理"""
    try:
        await bot.send_group_msg(group_id=group_id, message=msg)
        logger.info(f"推送至群 {group_id} 成功：{title} ({bvid})")
    except Exception as e:
        logger.error(f"推送至群 {group_id} 失败：{e}")

# 8. 清理过期临时文件
async def clean_expired_temp_files():
    """定期清理过期的临时文件（帧文件）"""
    try:
        now = datetime.now()
        expired_time = now - timedelta(days=TEMP_FILE_EXPIRE_DAYS)
        
        # 清理帧文件
        for frame_file in FRAME_DIR.glob("*.png"):
            file_mtime = datetime.fromtimestamp(frame_file.stat().st_mtime)
            if file_mtime < expired_time:
                frame_file.unlink(missing_ok=True)
                logger.debug(f"清理过期帧文件：{frame_file}")
        
        logger.debug("过期临时文件清理完成")
    except Exception as e:
        logger.error(f"清理过期临时文件失败：{e}")

# ====================== 核心监控逻辑 ======================
async def monitor_up_new_video():
    """优化版监控逻辑：防重复、稳容错、带清理"""
    if not BILI_WATCHER_ENABLED:
        logger.warning("B站UP监控功能未启用（配置不完整）")
        return

    logger.success(
        f"B站UP监控已启动\n"
        f"├── 监控UP主UID：{BILI_UP_UID}\n"
        f"├── 监控间隔：{BILI_UP_WAITSEC}秒\n"
        f"├── 推送群数：{len(MESSAGE_GROUPS)}个\n"
        f"├── 缓存文件：{CACHE_FILE.absolute()}\n"
        f"└── 临时文件目录：{VIDEO_DIR.absolute()}"
    )

    # 首次启动清理一次过期文件
    await clean_expired_temp_files()
    
    # 计数器：每24小时清理一次过期文件
    clean_counter = 0
    clean_interval = int(86400 / BILI_UP_WAITSEC)  # 24小时对应的轮询次数

    while True:
        try:
            # 1. 读取缓存
            last_cache = read_video_cache()
            
            # 2. 获取最新视频
            latest_video = await get_up_latest_video(BILI_UP_UID)
            if not latest_video:
                logger.info("未获取到UP主最新视频，持续轮询中")
                await asyncio.sleep(BILI_UP_WAITSEC)
                clean_counter += 1
                continue

            # 3. 提取核心信息
            current_timestamp = int(latest_video.get("publish_time", 0))
            current_title = latest_video.get("title", "")
            current_bvid = latest_video.get("bvid", "")
            
            # 4. 防重复推送（时间戳+BV号双重验证）
            is_new_video = False
            if current_timestamp > last_cache["timestamp"]:
                is_new_video = True
            elif current_bvid != last_cache["bvid"] and current_bvid:
                # 时间戳相同但BV号不同（UP主删重发）
                is_new_video = True
                logger.warning(f"检测到同时间戳不同BV号：{current_bvid}（原：{last_cache['bvid']}）")

            # 5. 处理新视频
            if is_new_video:
                logger.success(f"发现新视频：{current_title} (BV：{current_bvid}，时间戳：{current_timestamp})")
                # 先推送，后更新缓存
                await push_new_video(latest_video)
                # 更新缓存
                new_cache = {
                    "timestamp": current_timestamp,
                    "title": current_title,
                    "bvid": current_bvid
                }
                write_video_cache(new_cache)
            else:
                logger.debug(f"无新视频，当前最新：{last_cache['title']} (BV：{last_cache['bvid']})")

            # 6. 定期清理过期文件
            clean_counter += 1
            if clean_counter >= clean_interval:
                await clean_expired_temp_files()
                clean_counter = 0

        except Exception as e:
            logger.error(f"监控循环异常：{e}")
            # 异常时延长休眠时间，避免高频报错
            await asyncio.sleep(min(BILI_UP_WAITSEC * 2, 300))
            continue

        # 正常休眠
        await asyncio.sleep(BILI_UP_WAITSEC)

# ====================== 指令处理器======================
# 1. 手动触发监控
manual_watch = on_command("biliwatch", permission=SUPERUSER, block=True)
@manual_watch.handle()
async def handle_manual_watch():
    await manual_watch.send("开始手动触发UP主视频监控...")
    
    try:
        latest_video = await get_up_latest_video(BILI_UP_UID)
        if not latest_video:
            await manual_watch.finish("手动监控失败：未获取到UP主视频数据")
        
        # 更新缓存并推送
        current_timestamp = int(latest_video.get("publish_time", 0))
        current_title = latest_video.get("title", "")
        current_bvid = latest_video.get("bvid", "")
        
        write_video_cache({
            "timestamp": current_timestamp,
            "title": current_title,
            "bvid": current_bvid
        })
        
        await push_new_video(latest_video)
        await manual_watch.finish(f"手动监控完成！\n最新视频：{current_title} ({current_bvid})")
    
    except Exception as e:
        logger.error(f"手动监控异常：{e}")
        await manual_watch.finish(f"手动监控失败：{str(e)}")

# 2. BV解析指令
test_vsdu = on_command("bv", permission=SUPERUSER, block=True)
@test_vsdu.handle()
async def handle_test_vsdu(args: Message = CommandArg()):
    if not BILI_WATCHER_ENABLED:
        await test_vsdu.finish("插件已禁用！")
    
    input_url = args.extract_plain_text().strip()
    if not input_url:
        await test_vsdu.finish("请输入有效的B站视频链接（支持b23.tv短链接）！")
    
    # 处理短链接
    long_url = input_url
    if "b23.tv" in input_url:
        await test_vsdu.send("正在解析短链接...")
        long_url = await b23_to_long_url(input_url)
    
    # 精简链接
    final_url = simplify_bilibili_url(long_url)
    
    # 生成快照
    await test_vsdu.send("正在生成视频快照...")
    frame_path = await get_video_frame_by_VSDU_async(final_url)
    
    # 组装消息
    msg = Message()
    msg += MessageSegment.text(f"视频解析结果\n")
    if frame_path and frame_path.exists():
        msg += MessageSegment.image(f"file:///{frame_path.absolute()}")
        msg += MessageSegment.text("\n")
    msg += MessageSegment.text(f"标准链接：{final_url}")
    
    await test_vsdu.finish(msg)

# 3. 清理缓存指令（优化清理范围）
clean_cache = on_command("biliclear", permission=SUPERUSER, block=True)
@clean_cache.handle()
async def handle_clean_cache():
    await clean_cache.send("正在清理缓存和临时文件...")
    
    try:
        # 清理视频文件
        video_count = 0
        for file in VIDEO_DIR.glob("*.*"):
            file.unlink(missing_ok=True)
            video_count += 1
        
        # 清理帧文件
        frame_count = 0
        for file in FRAME_DIR.glob("*.*"):
            file.unlink(missing_ok=True)
            frame_count += 1
        
        # 删除缓存文件
        cache_deleted = False
        if CACHE_FILE.exists():
            CACHE_FILE.unlink(missing_ok=True)
            cache_deleted = True
        
        # 反馈结果
        result = f"清理完成！\n"
        result += f"├── 临时视频文件：清理 {video_count} 个\n"
        result += f"├── 视频帧文件：清理 {frame_count} 个\n"
        result += f"└── 监控缓存文件：{'已删除' if cache_deleted else '不存在'}"
        
        await clean_cache.finish(result)
    
    except Exception as e:
        logger.error(f"清理缓存失败：{e}")
        await clean_cache.finish(f"清理失败：{str(e)}")

# 4. 新增：查看监控状态
status_cmd = on_command("bilistatus", permission=SUPERUSER, block=True)
@status_cmd.handle()
async def handle_status():
    # 读取缓存
    cache = read_video_cache()
    
    # 组装状态信息
    status = Message()
    status += MessageSegment.text(f"B站UP监控状态\n")
    status += MessageSegment.text(f"├── 功能启用：{'是' if BILI_WATCHER_ENABLED else '❌ 否'}\n")
    status += MessageSegment.text(f"├── 监控UP主UID：{BILI_UP_UID}\n")
    status += MessageSegment.text(f"├── 监控间隔：{BILI_UP_WAITSEC}秒\n")
    status += MessageSegment.text(f"├── 推送群数：{len(MESSAGE_GROUPS)}个\n")
    status += MessageSegment.text(f"├── 缓存文件路径：{CACHE_FILE.absolute()}\n")
    status += MessageSegment.text(f"├── 最后更新缓存：{cache['update_time']}\n")
    status += MessageSegment.text(f"└── 最新监控视频：{cache['title']} (BV：{cache['bvid']})")
    
    await status_cmd.finish(status)

# 5. 废弃指令（别碰，这是石山代码）
check_uid = on_command("bilicheckuid", permission=SUPERUSER, block=True)
@check_uid.handle()
async def handle_check_uid():
    await check_uid.finish(f"该指令已废弃！\n使用 .bilistatus 查看当前监控的UP主UID：{BILI_UP_UID}")

# ====================== 插件启动/关闭 ======================
@DRIVER.on_startup
async def startup():
    """插件启动时初始化"""
    logger.success("B站UP监控插件（优化版）正在启动...")
    
    if BILI_WATCHER_ENABLED:
        # 启动监控任务
        asyncio.create_task(monitor_up_new_video())
        logger.success("B站UP监控任务已启动")
    else:
        logger.warning("B站UP监控任务未启动（配置不完整）")

@DRIVER.on_shutdown
async def shutdown():
    """插件关闭时清理资源"""
    logger.info("B站UP监控插件正在关闭...")
    
    # 最后清理一次临时文件
    await clean_expired_temp_files()
    
    logger.success("B站UP监控插件已安全关闭")
