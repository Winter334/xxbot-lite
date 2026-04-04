# xxbot

一期 Discord 修仙 Bot。

## 本地启动

> 需要 **Python 3.12**。若本机只有 Python 3.11，请先安装 3.12 再创建虚拟环境。

1. 创建虚拟环境并安装依赖：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

2. 复制 `.env.example` 为 `.env` 并填写 `DISCORD_TOKEN`。
3. 初始化数据库：

```powershell
alembic upgrade head
```

4. 启动 Bot：

```powershell
python -m bot.main
```

## 主要命令

- `/修仙`
- `/登塔`
- `/突破`
- `/榜单`
- `/面板`
- `/轮回`

## 测试

```powershell
pytest
```
