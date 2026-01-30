"""
看到隔壁湖大幻境社有这种很搞的功能，所以自己搞一个，也加进去玩玩
触发方式：群里直接发送文字「抽老婆」
"""
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment, Message
from nonebot.rule import Rule
import random
import json
import os
from datetime import date, datetime, timedelta

# ---------------------- 可自定义配置项 ----------------------
FILTER_SELF = True  # True=不抽自己，False=允许抽中自己
RECORD_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "Temp",
    "wife_choose_record.json"
)
CLEAN_DAYS = 7  # 自动清理超过N天的记录
# ---------------------- 结束 ----------------------

def is_choose_wife() -> Rule:
    async def _rule(event) -> bool:
        return isinstance(event, GroupMessageEvent) and event.get_plaintext().strip() == "抽老婆"
    return Rule(_rule)

choose_wife = on_message(rule=is_choose_wife(), block=True, priority=5)

def create_temp_dir():
    """自动创建Temp目录（若不存在）"""
    temp_dir = os.path.dirname(RECORD_FILE)
    if not os.path.exists(temp_dir):
        os.makedirs(temp_dir)
        print(f"【抽老婆小功能】自动创建目录：{temp_dir}")

def clean_expired_record(record: dict):
    """清理超过CLEAN_DAYS天的历史记录"""
    if not record:
        return record
    # 计算CLEAN_DAYS天前的日期（字符串格式：YYYY-MM-DD）
    expire_date = str(date.today() - timedelta(days=CLEAN_DAYS))
    expire_datetime = datetime.strptime(expire_date, "%Y-%m-%d")
    # 遍历所有群聊，删除过期日期的记录
    for group_id in list(record.keys()):
        group_record = record[group_id]
        for day in list(group_record.keys()):
            try:
                day_datetime = datetime.strptime(day, "%Y-%m-%d")
                if day_datetime < expire_datetime:
                    del group_record[day]
                    print(f"【抽老婆小功能】清理过期记录：群{group_id} {day}")
            except ValueError:
                # 日期格式错误则直接删除
                del group_record[day]
        if not group_record:
            del record[group_id]
    return record

def load_record():
    create_temp_dir()  # 加载前先确保Temp目录存在
    if os.path.exists(RECORD_FILE):
        try:
            with open(RECORD_FILE, "r", encoding="utf-8") as f:
                record = json.load(f)
            # 加载后自动清理过期记录
            record = clean_expired_record(record)
            return record
        except (json.JSONDecodeError, Exception) as e:
            print(f"【抽老婆小功能】加载记录失败，文件损坏：{str(e)}，返回空记录")
            return {}
    return {}

def save_record(record: dict):
    create_temp_dir()  # 保存前先确保Temp目录存在
    try:
        with open(RECORD_FILE, "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"【抽老婆小功能】保存记录失败：{str(e)}")

def get_today():
    return str(date.today())

@choose_wife.handle()
async def handle_choose_wife(bot: Bot, event: GroupMessageEvent):
    # 基础信息获取
    group_id = str(event.group_id)  
    user_id = event.user_id         
    today = get_today()             
    sender_at = MessageSegment.at(user_id)  
    bot_self_id = int(bot.self_id)  
    is_repeat = False  # 标记是否是重复抽取

    # 初始化抽取记录：结构为 record[群号][日期][发送者QQ] = 被抽中者QQ
    record = load_record()
    if group_id not in record:
        record[group_id] = {}
    if today not in record[group_id]:
        record[group_id][today] = {}

    # 获取群成员列表，过滤机器人自身
    member_list = await bot.get_group_member_list(group_id=int(group_id))
    # 过滤有效群成员：排除机器人 可选排除自己
    filter_conditions = []
    filter_conditions.append(lambda m: m["user_id"] != bot_self_id)  # 排除机器人
    if FILTER_SELF:
        filter_conditions.append(lambda m: m["user_id"] != user_id)
    valid_members = [m for m in member_list if all(cond(m) for cond in filter_conditions)]

    # 无有效群成员可抽的情况
    if not valid_members:
        await choose_wife.finish(f"{sender_at} 群里没人能抽啦～")

    lucky_id = None
    lucky_member = None
    if str(user_id) in record[group_id][today]:
        lucky_id = int(record[group_id][today][str(user_id)])
        # 验证已抽的人是否还在群里（防止退群）
        lucky_member = next((m for m in valid_members if m["user_id"] == lucky_id), None)
        # 有有效记录就标记为重复抽取
        if lucky_member: is_repeat = True
    # 无记录/已抽的人退群：重新随机抽取并更新记录
    if not lucky_member:
        lucky_member = random.choice(valid_members)
        lucky_id = lucky_member["user_id"]
        record[group_id][today][str(user_id)] = lucky_id
        save_record(record)

    lucky_name = lucky_member["card"] or lucky_member["nickname"] or "幸运群友"
    # QQ官方高清头像接口
    lucky_avatar = MessageSegment.image(f"https://q1.qlogo.cn/g?b=qq&nk={lucky_id}&s=640")

    # 构造最终消息
    if is_repeat:
    # 重复抽取：专属提醒语（按你要的格式）
        final_msg = (
            f"{sender_at}\n"
            f"你今天已经有老婆了哦~请好好对待呢~\n"
            f"{lucky_avatar}\n"
            f"{lucky_name}"
        )
    else:
        # 首次抽取：保留原基础格式
        final_msg = (
            f"{sender_at}\n"
            f"你今天的群友老婆是：\n"
            f"{lucky_avatar}\n"
            f"{lucky_name}"
        )
    await choose_wife.finish(Message(final_msg))