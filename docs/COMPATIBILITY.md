# 跨平台兼容性规范

> 项目需同时支持 Linux 和 Windows 部署。所有代码必须遵循以下规范。

## 路径操作

```python
# ✅ 正确：用 os.path.join
path = os.path.join("data", "quant_stocks.db")

# ✅ 正确：用 __file__ 推断项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
db_path = os.path.join(PROJECT_ROOT, "data", "quant_stocks.db")

# ❌ 错误：硬编码 Linux 路径
path = "/root/data/quant_stocks.db"

# ❌ 错误：硬编码 Windows 路径
path = r"C:\Users\root\data\quant_stocks.db"

# ❌ 错误：手动拼接分隔符
path = "data" + "/" + "quant_stocks.db"
```

## 环境变量

```python
# ✅ 正确：用环境变量 + 相对路径兜底
data_dir = os.environ.get("BACKTEST_DATA_DIR", os.path.join(PROJECT_ROOT, "data"))

# ❌ 错误：硬编码 HOME 目录
data_dir = os.path.expanduser("~/data")
```

## 换行符

```python
# ✅ 正确：写文件时显式指定 newline
with open(path, "w", newline="\n") as f:
    f.write(content)

# ✅ 正确：读文件时用通用换行模式（默认）
with open(path, "r") as f:
    content = f.read()
```

## 禁止使用的 API

| API | 原因 | 替代方案 |
|-----|------|---------|
| `os.fork()` | Windows 不支持 | `multiprocessing.Process` |
| `os.getuid()` / `os.getgid()` | Windows 无 UID/GID | `os.getlogin()` 或环境变量 |
| `signal.SIGTERM` | Windows 信号支持有限 | `try/finally` + 文件锁 |
| `os.sep` 直接比较 | 用 `os.path.join` 自动处理 | `os.path.join` |
| `sys.platform == "linux"` | 不精确 | `sys.platform.startswith("linux")` |

## 依赖

- 只使用跨平台库（pandas/numpy/requests/sqlite3 等）
- 避免使用 Linux 专属库（如 `fcntl`、`pty`）
- 如必须使用平台相关代码，用 `sys.platform` 分支：

```python
import sys

if sys.platform == "win32":
    # Windows 特定代码
    pass
else:
    # Linux/Mac 代码
    pass
```

## 测试

- 测试文件中的路径必须用 `os.path.join(PROJECT_ROOT, ...)` 构造
- 禁止在测试中硬编码 `/root/`、`/home/`、`C:\` 等绝对路径
