"""
当时是看到湖大幻境社的机器人有这个功能所以做了，
我当时写完这个功能，因为要和meal_choose.json做适配，
那个图片我不打算存到本地，那样会导致文件过大，
你知道吗，这些个食物图片，我当时去搜的时候，还没吃午饭，
然后我还是减肥期间，不能吃好的食物，
看到一个个好吃的美食图片，那种可望不可及的感觉，
一下就后悔写这个功能了，妈的差点把我馋死，
一边搜一边咽口水，望着我那个鸡胸肉瞬间不香了。
这些个图片都是从昵享网那里获取的，所以如果哪一天没法用了，为了我的减肥，我是懒得维护了，
麻烦各位手动加上去。
20260130 糊糊留
"""
from nonebot import on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment, Message
from nonebot.rule import Rule
import random
import json
import os

# 配置文件路径（根目录BotServer/Config/meal_choose.json）
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "Config", "meal_choose.json")

def load_meal_config():
    """加载外置的meal_choose.json配置文件"""
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"配置文件不存在：{CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

# 加载食物+图片配置
MEAL_CONFIG = load_meal_config()

def is_ask_meal() -> Rule:
    """触发规则：群消息 + 早上/中午/晚上+吃什么"""
    async def _rule(event) -> bool:
        if not isinstance(event, GroupMessageEvent):
            return False
        plain_text = event.get_plaintext().strip()
        return any(plain_text.startswith(t) and "吃什么" in plain_text for t in ["早上", "中午", "晚上"])
    return Rule(_rule)

ask_meal = on_message(rule=is_ask_meal(), block=True, priority=5)

@ask_meal.handle()
async def handle_meal(event: GroupMessageEvent):
    # 提取触发时段
    plain_text = event.get_plaintext().strip()
    if plain_text.startswith("早上"):
        time_period = "早上"
    elif plain_text.startswith("中午"):
        time_period = "中午"
    else:
        time_period = "晚上"
    
    # 从配置中获取对应时段的食物+图片
    food_dict = MEAL_CONFIG[time_period]
    random_food = random.choice(list(food_dict.keys()))
    food_img_url = food_dict[random_food]
    # 构造图片消息段（无本地存储，直接加载网络链接）
    # 核心修改：添加图片尺寸限制（统一300x300，比例一致不畸变）
    # OneBot协议支持在图片链接后拼接size参数限制尺寸，格式：url?size=宽x高
    if food_img_url:  # 仅当图片链接不为空时添加尺寸限制
        food_img_url = f"{food_img_url}?size=600x450"
    food_img = MessageSegment.image(food_img_url)
    
    # 按指定格式回复
    final_msg = (
        f"🍽这里建议你吃🍽\n"
        f"{random_food}\n"
        f"{food_img}"
    )
    await ask_meal.finish(Message(final_msg))