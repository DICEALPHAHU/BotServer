from Scripts.Config import config

uuid_caches: dict[str, str] = {}
cpu_occupation: dict[str, list] = {}
ram_occupation: dict[str, list] = {}
tps_occupation: dict[str, list] = {} 
mspt_occupation: dict[str, list] = {} 
# 采集X轴真实时间
cpu_time: dict[str, list] = {}  
ram_time: dict[str, list] = {}  
tps_time: dict[str, list] = {}  
mspt_time: dict[str, list] = {} 
MAX_HISTORY_LENGTH = 10  
# ========================================================

render_template = None

if config.image_mode:
    from .Render import render_template

# LtNsttMj1tUSaieZRjvHHk2h2AZOEKIG
# https://crafatar.com/avatars/{uuid}
