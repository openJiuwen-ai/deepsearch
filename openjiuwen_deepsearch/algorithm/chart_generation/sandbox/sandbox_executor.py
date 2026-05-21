# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
沙箱代码执行器 — 子进程隔离版

通过独立 Python 子进程执行 LLM 生成的代码，确保主进程的环境隔离与安全。

安全机制：
1. 进程隔离  — 代码在独立子进程中运行，崩溃 / 内存泄漏不影响主进程
2. 模块限制  — import hook 拦截危险模块（subprocess, shutil, ctypes 等）
3. 写入限制  — 仅允许向工作目录及系统临时目录写入文件
4. 读取限制  — 仅允许读取工作目录、临时目录、系统库目录等安全白名单路径
               禁止读取 /etc、/root、/home 等系统敏感路径
5. 系统调用限制 — 禁用 os.system, os.popen 等可执行外部命令的函数
6. 超时控制  — 执行超时自动 SIGKILL 终止子进程
7. HMAC 验证  — 使用 HMAC 签名验证结果文件完整性，防止子进程伪造输出
               文件路径由父进程预先确定，不从子进程 stdout 解析
8. 环境隔离  — 子进程使用最小化环境变量，防止敏感信息泄露
9. 输出脱敏  — stdout/stderr 过滤敏感信息，防止通过错误反馈链外传
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import uuid
from typing import Any, Dict, List

_sandbox_logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════
# 安全常量配置
# ════════════════════════════════════════════════════════════════

# 子进程运行所需的最小环境变量白名单
# 仅包含 Python 运行和科学计算库所需的关键变量
SAFE_ENV_WHITELIST: List[str] = [
    "PATH",
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONIOENCODING",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "HOME",
    "TMP",
    "TMPDIR",
    "TEMP",
    "MPLCONFIGDIR",      # matplotlib 配置目录
    "OPENBLAS_NUM_THREADS",  # numpy 性能优化
    "OMP_NUM_THREADS",       # OpenMP 线程数
]

# 敏感信息匹配模式（用于脱敏 stdout/stderr）
SENSITIVE_PATTERNS: List[tuple] = [
    # API keys and tokens
    (re.compile(r'(api[_-]?key|apikey|token|secret|password|passwd|pwd|credential)[\s=:]+[^\s]+', re.IGNORECASE),
     r'\1=<REDACTED>'),
    # AWS credentials
    (re.compile(r'(aws[_-]?access[_-]?key[_-]?id|aws[_-]?secret[_-]?access[_-]?key)[\s=:]+[^\s]+', re.IGNORECASE),
     r'\1=<REDACTED>'),
    # Database URLs
    (re.compile(r'(postgres|mysql|mongodb|redis)://[^\s]+', re.IGNORECASE),
     r'\1://<REDACTED>@<REDACTED>'),
    # JWT tokens
    (re.compile(r'eyJ[a-zA-Z0-9_-]*\.eyJ[a-zA-Z0-9_-]*\.[a-zA-Z0-9_-]*'), '<JWT_REDACTED>'),
    # Private keys markers
    (re.compile(
        r'-----BEGIN\s+(RSA\s+|DSA\s+|EC\s+|OPENSSH\s+)?'
        r'PRIVATE\s+KEY-----[\s\S]*-----END\s+.*PRIVATE\s+KEY-----'
    ), '<PRIVATE_KEY_REDACTED>'),
]

RESTRICTED_MODULES = frozenset(
    {
        # 命令执行相关
        "subprocess",
        "shutil",
        "ctypes",
        "pty",
        "pexpect",
        "paramiko",
        "asyncio.subprocess",
        # 进程控制相关
        "signal",
        "multiprocessing",
        "threading",  # 可能被用于创建恶意线程
        # 网络/通信相关
        "socket",
        "http.server",
        "xmlrpc",
        "ftplib",
        "smtplib",
        "webbrowser",
        "telnetlib",
        "poplib",
        "imaplib",
        "nntplib",
        "urllib.request",  # 可用于下载恶意内容
        "requests",       # HTTP 客户端
        "aiohttp",
        # 代码执行相关
        "code",
        "codeop",
        "compileall",
        "exec",
        "builtins",  # 防止重新导入原始 builtins
        # 动态执行相关
        "pickle",     # pickle 可执行任意代码
        "shelve",
        "marshal",
        "importlib.util",  # 可用于动态导入
        # 文件系统危险操作
        "tempfile",   # 虽然我们允许临时目录，但不允许直接使用 tempfile 模块
        # 其他危险模块
        "gc",         # gc 可能影响内存管理
        "traceback",  # traceback.print_tb 可能泄露信息
        "dis",        # 反汇编模块
        "inspect",    # 可用于获取源代码等
    }
)

DEFAULT_EXEC_TIMEOUT = 120

_FONT_CANDIDATES = [
    os.path.join(os.path.dirname(__file__), "..", "fonts", "kt_font.ttf"),
    os.path.join(
        "openjiuwen_deepsearch", "algorithm", "chart_generation", "fonts", "kt_font.ttf"
    ),
]

# ────────────────────────────────────────────────────────────────
# Worker script — 在隔离子进程中执行，通过 stdin 注入
# ────────────────────────────────────────────────────────────────
_WORKER_SCRIPT = r'''
import sys, os, json, builtins, traceback, tempfile, importlib, hashlib, hmac

_cfg = json.loads(os.environ["_SANDBOX_CFG"])
_working_dir = os.path.abspath(_cfg["working_dir"])
_variables   = _cfg.get("variables", {})
_restricted  = frozenset(_cfg.get("restricted_modules", []))
_font_path   = _cfg.get("font_path", "")
_code_path   = _cfg["code_path"]
_chart_result_path = _cfg.get("chart_result_path", "")
_hmac_secret = _cfg.get("hmac_secret", "")

# ════════════════════════════════════════════════════════════════
# Phase 1: 在无任何限制的环境下预加载所有受信任的科学计算库
#   matplotlib / numpy / pandas / seaborn 等库在初始化时会内部
#   import shutil, ctypes, signal 等模块；这些属于库的实现细节，
#   必须在安全限制激活之前完成加载。
#   每个库独立 try/except，避免一个失败导致后续库都跳过。
# ════════════════════════════════════════════════════════════════
import os as _os
_orig_open = builtins.open

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager as _fm
    if _font_path and _os.path.isfile(_font_path):
        _fm.fontManager.addfont(_font_path)
        _font_name = _fm.FontProperties(fname=_font_path).get_name()
        plt.rcParams["font.family"] = _font_name
    matplotlib.rcParams["axes.unicode_minus"] = False
except Exception:
    pass

try:
    import numpy
except Exception:
    pass

try:
    import pandas
except Exception:
    pass

try:
    import seaborn
except Exception:
    pass

# ════════════════════════════════════════════════════════════════
# Phase 2: 所有受信任库已加载完毕，现在激活安全限制
#   限制只约束后续执行的"用户代码"，不影响已加载的库内部逻辑。
# ════════════════════════════════════════════════════════════════

# ── Import restriction ──────────────────────────────────────────
_orig_import = builtins.__import__

def _safe_import(name, *args, **kwargs):
    if name.split(".")[0] in _restricted:
        raise ImportError(f"Importing '{name}' is not allowed in the code sandbox.")
    return _orig_import(name, *args, **kwargs)

builtins.__import__ = _safe_import

_orig_import_module = importlib.import_module

def _safe_import_module(name, package=None):
    if name.split(".")[0] in _restricted:
        raise ImportError(f"Importing '{name}' is not allowed in the code sandbox.")
    return _orig_import_module(name, package)

importlib.import_module = _safe_import_module

# ── sys.modules cleanup and encapsulation ───────────────────────────
# Phase 1 可能已经预加载了某些危险模块（如 ctypes，某些库内部使用）
# 需要从 sys.modules 中移除并封装，防止用户代码通过 sys.modules 访问
_dangerous_loaded_modules = []
for _mod_name in ['ctypes', '_ctypes', 'subprocess', 'shutil', 'pickle', 'marshal']:
    if _mod_name in sys.modules:
        _dangerous_loaded_modules.append((_mod_name, sys.modules[_mod_name]))
        del sys.modules[_mod_name]

# 封装 sys.modules，防止用户代码直接访问危险模块
class _SafeModulesDict(dict):
    """安全的 sys.modules 包装器，阻止访问危险模块"""
    def __getitem__(self, key):
        if key.split('.')[0] in _restricted:
            raise ImportError(f"Access to module '{key}' is blocked in sandbox.")
        return super().__getitem__(key)

    def get(self, key, default=None):
        if key.split('.')[0] in _restricted:
            return default  # 返回 None 而不是模块
        return super().get(key, default)

    def __contains__(self, key):
        if key.split('.')[0] in _restricted:
            return False  # 隐藏危险模块的存在
        return super().__contains__(key)

# 创建安全包装的 sys.modules
_safe_modules = _SafeModulesDict(sys.modules)
sys.modules = _safe_modules

# 清理临时变量，防止用户代码通过 inspect.getsource() 等方式访问
del _dangerous_loaded_modules
del _SafeModulesDict
del _safe_modules

# ── Preloaded modules security encapsulation ───────────────────────
# 预加载模块在安全限制激活前加载，可能包含危险的入口点。
# 尝试对预加载模块进行安全封装，禁用可能导致命令执行的功能。
# matplotlib: switch_backend() 可加载其他 backend，可能执行代码
# pandas: eval() 可执行任意表达式
# numpy: ctypeslib 可调用 C 函数
try:
    import matplotlib.pyplot as _plt_ref
    import matplotlib.backends as _backends
    # 安全 backend 白名单：只允许非交互式 backend
    # Agg 系列是纯图像渲染 backend，不会打开 GUI 窗口或执行系统命令
    _SAFE_BACKENDS = frozenset(['agg', 'module://backend_interagg', 'module://matplotlib_backend_interagg'])

    # 获取原始的 switch_backend 函数
    _orig_switch_backend = _plt_ref.switch_backend

    def _safe_switch_backend(name=None):
        """安全的 backend 切换：只允许非交互式 backend"""
        # 如果没有指定 name，使用默认行为（通常是检查当前 backend）
        if name is None:
            return _orig_switch_backend(name)
        # 检查 backend 名称是否在安全白名单中
        backend_name = name.lower() if isinstance(name, str) else str(name).lower()
        if backend_name in _SAFE_BACKENDS or backend_name.replace('module://', '') in _SAFE_BACKENDS:
            return _orig_switch_backend(name)
        # 危险 backend：GUI backend 可能执行系统命令或打开窗口
        raise PermissionError(
            f"matplotlib backend '{name}' is not allowed in sandbox. "
            f"Only non-interactive backends ({_SAFE_BACKENDS}) are permitted."
        )
    _plt_ref.switch_backend = _safe_switch_backend

    # 禁用 ion/ioff 交互模式切换（防止意外交互行为）
    _plt_ref.ion = lambda: None
    _plt_ref.ioff = lambda: None
    # 禁用 show()（虽然是 Agg backend，但防止意外调用）
    _plt_ref.show = lambda *a, **kw: None
except Exception:
    pass

try:
    import pandas as _pd_ref
    # 禁用 pd.eval()，防止执行任意表达式
    def _blocked_eval(*a, **kw):
        raise PermissionError("pandas.eval() is not allowed in sandbox.")
    _pd_ref.eval = _blocked_eval
    # 禁用 pd.DataFrame.query() 的 parser='python' 模式
    # 通过覆盖默认 parser 参数实现限制
    _original_query = _pd_ref.DataFrame.query
    def _safe_query(self, expr, **kwargs):
        # 强制使用 'numexpr' 或 'pandas' parser，禁止 'python'
        kwargs['parser'] = 'numexpr' if 'numexpr' in str(_original_query.__code__.co_varnames) else 'pandas'
        return _original_query(self, expr, **kwargs)
    _pd_ref.DataFrame.query = _safe_query
except Exception:
    pass

try:
    import numpy as _np_ref
    # 禁用 numpy.ctypeslib 相关功能（防止调用 C 函数）
    if hasattr(_np_ref, 'ctypeslib'):
        _np_ref.ctypeslib = None
except Exception:
    pass

# ── File write restriction ──────────────────────────────────────
_write_dirs = [_working_dir, os.path.abspath(tempfile.gettempdir())]
_home_cache = os.path.join(os.path.expanduser("~"), ".cache")
if os.path.isdir(_home_cache):
    _write_dirs.append(os.path.abspath(_home_cache))

# ── File read restriction ──────────────────────────────────────
# 安全读取路径白名单：只允许读取工作目录、临时目录、系统库目录等
_read_dirs = [_working_dir, os.path.abspath(tempfile.gettempdir())]
# 添加 matplotlib 配置目录
if os.path.isdir(_home_cache):
    _read_dirs.append(os.path.abspath(_home_cache))
# 添加 Python 标准库路径（允许 import 加载模块）
_stdlib_paths = []
# os.__file__ 指向标准库目录
if hasattr(os, '__file__') and os.__file__:
    _stdlib_paths.append(os.path.abspath(os.path.dirname(os.__file__)))
# sys.prefix 指向 Python 安装目录（包含标准库）
if hasattr(sys, 'prefix') and sys.prefix:
    _stdlib_paths.append(os.path.abspath(sys.prefix))
    _lib_dir = os.path.join(sys.prefix, 'lib')
    if os.path.isdir(_lib_dir):
        _stdlib_paths.append(os.path.abspath(_lib_dir))
for _p in _stdlib_paths:
    if os.path.isdir(_p) and _p not in _read_dirs:
        _read_dirs.append(_p)
# 添加第三方库安装路径（允许 import matplotlib/numpy/pandas 等）
_site_packages = []
try:
    import site
    _site_packages = site.getsitepackages()
except Exception:
    pass
for _p in _site_packages:
    if os.path.isdir(_p):
        _abs_p = os.path.abspath(_p)
        if _abs_p not in _read_dirs:
            _read_dirs.append(_abs_p)
# 允许读取字体文件
if _font_path and os.path.isfile(_font_path):
    _font_dir = os.path.abspath(os.path.dirname(_font_path))
    if _font_dir not in _read_dirs:
        _read_dirs.append(_font_dir)

def _safe_open(file, mode="r", *a, **kw):
    _abs = os.path.abspath(str(file))
    # 写入限制：只允许写入工作目录和临时目录
    if any(m in mode for m in ("w", "a", "x", "+")):
        if not any(_abs == d or _abs.startswith(d + os.sep) for d in _write_dirs):
            raise PermissionError(
                f"Writing to '{file}' is not allowed. "
                f"Sandbox writes restricted to: {_write_dirs}"
            )
    # 读取限制：只允许读取安全白名单路径
    # 防止恶意代码读取服务账号可访问的任意敏感文件
    elif mode in ("r", "rb", "rt") or "r" in mode:
        # 允许读取不存在的文件（后续操作会自然报错）
        # 但禁止读取敏感路径下的文件
        if not any(_abs == d or _abs.startswith(d + os.sep) for d in _read_dirs):
            # 检查是否是系统配置文件等高敏感路径
            _high_risk_paths = [
                "/etc", "/root", "/home", "/var/log", "/var/lib",
                "/proc", "/sys", "/dev", "/run", "/opt",
                "/usr/share", "/usr/local/etc",
            ]
            for _risk in _high_risk_paths:
                if _abs.startswith(_risk + "/") or _abs == _risk:
                    raise PermissionError(
                        f"Reading system path '{file}' is not allowed in sandbox. "
                        f"Possible security violation detected."
                    )
            raise PermissionError(
                f"Reading '{file}' is not allowed. "
                f"Sandbox reads restricted to safe directories."
            )
    return _orig_open(file, mode, *a, **kw)

builtins.open = _safe_open

# ── Dangerous os functions ──────────────────────────────────────
for _fn_name in (
    "system", "popen",
    "execl", "execle", "execlp", "execlpe",
    "execv", "execve", "execvp", "execvpe",
    "spawnl", "spawnle", "spawnlp", "spawnlpe",
    "spawnv", "spawnve", "spawnvp", "spawnvpe",
    "kill", "killpg",
):
    if hasattr(_os, _fn_name):
        def _blocked(*a, _n=_fn_name, **kw):
            raise PermissionError(f"os.{_n}() is not allowed in the sandbox.")
        setattr(_os, _fn_name, _blocked)

# ════════════════════════════════════════════════════════════════
# Phase 3: 构建 exec 命名空间并执行用户代码
#   使用隔离命名空间和受限 builtins 执行用户代码，
#   防止访问沙箱内部变量和危险内置函数。
# ════════════════════════════════════════════════════════════════

# 保存原始 open 函数供 finally 块使用（在隔离命名空间外）
__sandbox_orig_open_ref__ = _orig_open

# ── 构建 exec 命名空间（先创建，供安全函数引用） ──────────────
_exec_namespace = {}

# ── 构建受限 builtins ──────────────────────────────────────────
_safe_builtins = {}

# 安全的内置函数白名单
_SAFE_BUILTINS_WHITELIST = frozenset([
    # 基本类型和转换
    'int', 'float', 'str', 'bool', 'list', 'dict', 'tuple', 'set', 'frozenset',
    'bytes', 'bytearray', 'complex', 'range', 'slice',
    # 数学和逻辑
    'abs', 'all', 'any', 'bin', 'chr', 'ord', 'divmod', 'hex', 'oct',
    'max', 'min', 'pow', 'round', 'sum', 'len', 'sorted', 'reversed',
    # 类型检查
    'isinstance', 'issubclass', 'type', 'callable', 'hasattr',
    # 迭代和生成器
    'iter', 'next', 'enumerate', 'zip', 'map', 'filter',
    # 字符串处理
    'format', 'repr', 'ascii', 'print',
    # 属性访问
    'getattr', 'setattr', 'delattr',
    # 常量
    'None', 'True', 'False', 'Ellipsis',
    # 异常（图表代码可能需要捕获）
    'Exception', 'ValueError', 'TypeError', 'KeyError', 'IndexError',
    'AttributeError', 'RuntimeError', 'StopIteration',
])

# 从当前 builtins 中复制白名单函数
for _name in _SAFE_BUILTINS_WHITELIST:
    if hasattr(builtins, _name):
        _safe_builtins[_name] = getattr(builtins, _name)

# 使用安全封装的 open 和 __import__
_safe_builtins['open'] = builtins.open
_safe_builtins['__import__'] = builtins.__import__

# 禁用危险函数
def _blocked_builtin(*a, **kw):
    raise PermissionError("This built-in function is not allowed in sandbox.")
for _name in ['eval', 'exec', 'compile', 'breakpoint', 'input']:
    _safe_builtins[_name] = _blocked_builtin

# 安全的 globals/locals/dir/vars - 只返回用户命名空间
_safe_builtins['globals'] = lambda: dict(_exec_namespace)
_safe_builtins['locals'] = lambda: dict(_exec_namespace)
_safe_builtins['dir'] = lambda obj=None: sorted(_exec_namespace.keys()) if obj is None else [_n for _n in dir(obj) if not _n.startswith('_sandbox_')]
_safe_builtins['vars'] = lambda obj=None: dict(_exec_namespace) if obj is None else vars(obj) if hasattr(obj, '__dict__') else None

# 完成命名空间构建
_exec_namespace["__builtins__"] = _safe_builtins

# 注入常用科学计算模块到隔离命名空间
for _inject_alias, _inject_mod_name in {
    "os": "os", "json": "json", "math": "math", "re": "re",
    "matplotlib": "matplotlib",
    "plt": "matplotlib.pyplot",
    "fm": "matplotlib.font_manager",
    "font_manager": "matplotlib.font_manager",
    "np": "numpy", "numpy": "numpy",
    "pd": "pandas", "pandas": "pandas",
    "sns": "seaborn", "seaborn": "seaborn",
}.items():
    try:
        if _inject_mod_name in sys.modules:
            _exec_namespace[_inject_alias] = sys.modules[_inject_mod_name]
    except Exception:
        pass

# 注入用户变量到隔离命名空间
for _var_name, _var_value in _variables.items():
    _exec_namespace[_var_name] = _var_value

# 读取用户代码（使用原始 open，在隔离命名空间外）
with _orig_open(_code_path, "r", encoding="utf-8") as _code_file:
    _user_code_content = _code_file.read()

# 执行用户代码（使用隔离命名空间，不是 globals()）
# 关键安全点：用户代码无法访问沙箱内部变量如 _orig_open, _restricted 等
try:
    exec(compile(_user_code_content, "<sandbox>", "exec"), _exec_namespace)
except Exception:
    traceback.print_exc()
    sys.exit(1)
finally:
    # finally 块在隔离命名空间外运行，可以访问沙箱内部变量
    try:
        import matplotlib.pyplot as _plt
        import base64 as _b64
        import io as _io
        # 获取所有活跃的figures并转换为base64
        _figs = [_plt.figure(i) for i in _plt.get_fignums()]
        if _figs:
            _buf = _io.BytesIO()
            _figs[0].savefig(_buf, format='png', bbox_inches='tight')
            _buf.seek(0)
            _chart_b64 = _b64.b64encode(_buf.read()).decode('utf-8')
            _buf.close()
            # 安全机制：只写入预固定路径，使用 HMAC 签名验证
            # 路径由父进程预先确定，子进程无法伪造
            if _chart_result_path:
                # 验证路径在工作目录内（防止路径穿越）
                _abs_chart_path = os.path.abspath(_chart_result_path)
                if _abs_chart_path.startswith(_working_dir + os.sep) or _abs_chart_path == _working_dir:
                    with __sandbox_orig_open_ref__(_chart_result_path, "w", encoding="utf-8") as _cf:
                        _cf.write(_chart_b64)
                    # 计算 HMAC 签名，证明文件内容完整性
                    # 父进程只信任 HMAC 签名，不信任任何 stdout 输出的路径字符串
                    if _hmac_secret:
                        _sig = hmac.new(_hmac_secret.encode(), _chart_b64.encode(), hashlib.sha256).hexdigest()
                        print(f"__CHART_RESULT_SIGNATURE__: {_sig}")
        _plt.close("all")
        # 显式释放内存
        del _figs
        del _buf
        del _chart_b64
    except Exception:
        pass
'''


def _resolve_font_path() -> str:
    """定位 kt_font.ttf 字体文件，按候选路径依次查找。"""
    for candidate in _FONT_CANDIDATES:
        abs_path = os.path.abspath(candidate)
        if os.path.isfile(abs_path):
            return abs_path
    return ""


def _build_safe_env() -> dict:
    """
    构建最小化的安全环境变量字典。

    只传递 Python 运行和科学计算库所需的关键环境变量，
    防止敏感信息（API keys、密钥等）通过环境变量泄露给子进程。

    Returns:
        dict: 安全的环境变量字典
    """
    safe_env = {}
    for key in SAFE_ENV_WHITELIST:
        if key in os.environ:
            safe_env[key] = os.environ[key]
    return safe_env


def _sanitize_output(text: str, max_length: int = 2000) -> str:
    """
    脱敏输出内容，移除敏感信息并限制长度。

    用于过滤子进程的 stdout/stderr，防止敏感信息通过错误反馈链
    泄露给 LLM 服务商。

    Args:
        text: 原始输出文本
        max_length: 最大输出长度（防止大量输出）

    Returns:
        str: 脱敏后的安全输出
    """
    if not text:
        return text

    sanitized = text

    # 应用敏感信息匹配模式
    for pattern, replacement in SENSITIVE_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)

    # 过滤可能包含大量环境变量 dump 的输出
    # 检测连续多行的 key=value 格式（可能是 os.environ 打印）
    lines = sanitized.split('\n')
    env_dump_count = 0
    filtered_lines = []
    for line in lines:
        # 检测类似环境变量打印的格式
        if re.match(r'^[A-Za-z_][A-Za-z0-9_]*=.+$', line.strip()):
            env_dump_count += 1
            if env_dump_count > 10:  # 超过10行视为环境变量dump，跳过
                continue
            # 对这些行也进行脱敏
            line = re.sub(
                r'^([A-Za-z_][A-Za-z0-9_]*=).+$',
                r'\1<REDACTED>',
                line
            )
        else:
            env_dump_count = 0  # 重置计数
        filtered_lines.append(line)

    sanitized = '\n'.join(filtered_lines)

    # 限制输出长度，防止大量输出造成问题
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length] + "\n... [OUTPUT TRUNCATED FOR SECURITY]"

    return sanitized


class AsyncCodeExecutor:
    """
    子进程隔离的 Python 代码沙箱。

    每次 execute() 启动一个独立 Python 子进程来执行代码:
    - 主进程环境不受污染（变量、模块、matplotlib 状态等）
    - 代码崩溃/段错误不影响主进程
    - 内存泄漏随子进程退出自动回收
    - 通过 set_variable() 注入的变量以 JSON 序列化方式传递给子进程
    """

    def __init__(self, working_dir: str, exec_timeout: float = DEFAULT_EXEC_TIMEOUT):
        self.working_dir = os.path.abspath(working_dir)
        self.exec_timeout = exec_timeout
        self.session_id = str(uuid.uuid4())
        self._variables: Dict[str, Any] = {}
        self._font_path = _resolve_font_path()
        os.makedirs(self.working_dir, exist_ok=True)

    def set_variable(self, name: str, value: Any):
        """注入变量到沙箱执行命名空间。"""
        self._variables[name] = value

    def get_variable(self, name: str) -> Any:
        """获取已注入的变量值。"""
        return self._variables.get(name)

    async def execute(self, code: str) -> dict:
        """
        在隔离子进程中执行代码。

        流程：
        1. 将用户代码写入临时 .py 文件
        2. 通过环境变量传递沙箱配置（变量、受限模块、字体路径等）
        3. 启动子进程，通过 stdin 注入 worker 引导脚本
        4. worker 在子进程中设置安全限制后执行用户代码
        5. 捕获 stdout/stderr，超时则 SIGKILL 终止
        6. 从预固定路径读取base64数据，使用 HMAC 验证完整性

        安全机制：
        - 文件路径由父进程预先确定，不从 stdout 解析
        - 使用 HMAC 签名验证文件内容完整性，防止子进程伪造

        Returns:
            {"stdout": str, "stderr": str, "error": bool, "chart_base64": str}
        """
        tag = uuid.uuid4().hex[:12]
        # 预固定路径 - 父进程控制，子进程无法覆盖
        code_path = os.path.join(self.working_dir, f"_sandbox_{tag}.py")
        chart_result_file = os.path.join(self.working_dir, f"_chart_result_{tag}.tmp")
        # 生成 HMAC 密钥用于验证结果文件完整性
        hmac_secret = uuid.uuid4().hex

        try:
            with open(code_path, "w", encoding="utf-8") as f:
                f.write(code)

            config = {
                "working_dir": self.working_dir,
                "variables": self._safe_serialize(self._variables),
                "restricted_modules": list(RESTRICTED_MODULES),
                "font_path": self._font_path,
                "code_path": os.path.abspath(code_path),
                "chart_result_path": chart_result_file,
                "hmac_secret": hmac_secret,  # HMAC 验证密钥
            }

            # 安全机制：使用最小化的环境变量，防止敏感信息泄露给子进程
            # 只传递 Python 运行必需的白名单变量，不传递 API keys 等敏感配置
            env = _build_safe_env()
            env["_SANDBOX_CFG"] = json.dumps(config, ensure_ascii=False, default=str)

            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=self.working_dir,  # 子进程在工作目录运行，确保相对路径安全
            )

            try:
                stdout_raw, stderr_raw = await asyncio.wait_for(
                    proc.communicate(input=_WORKER_SCRIPT.encode("utf-8")),
                    timeout=self.exec_timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {
                    "stdout": "",
                    "stderr": (
                        f"ExecutionTimeout: code execution exceeded "
                        f"{self.exec_timeout}s limit\n"
                    ),
                    "error": True,
                    "chart_base64": None,
                }

            stdout = stdout_raw.decode("utf-8", errors="replace")
            stderr = stderr_raw.decode("utf-8", errors="replace")

            # 安全读取图表结果：使用预固定路径 + HMAC 验证
            chart_base64 = None
            signature_marker = "__CHART_RESULT_SIGNATURE__:"
            if signature_marker in stdout:
                # 提取 HMAC 签名（不是文件路径！）
                idx = stdout.find(signature_marker) + len(signature_marker)
                received_sig = stdout[idx:].strip().split('\n')[0]
                # 清理 stdout 中的签名标记
                stdout = stdout[:stdout.find(signature_marker)].strip()

                # 只读取预固定的路径（安全：路径由父进程控制）
                if os.path.exists(chart_result_file):
                    try:
                        # 验证路径在工作目录内（防御路径穿越）
                        abs_chart_path = os.path.abspath(chart_result_file)
                        if not (abs_chart_path.startswith(self.working_dir + os.sep) or
                                abs_chart_path == self.working_dir):
                            _sandbox_logger.warning(
                                "Security: chart result path escape attempt blocked: %s",
                                chart_result_file
                            )
                            return {
                                "stdout": stdout if stdout.strip() else "Run completed with no output.",
                                "stderr": stderr + "\nSecurity: invalid result path\n",
                                "error": True,
                                "chart_base64": None,
                            }

                        with open(chart_result_file, "r", encoding="utf-8") as cf:
                            content = cf.read()

                        # HMAC 验证：确保文件内容未被篡改
                        expected_sig = hmac.new(
                            hmac_secret.encode(),
                            content.encode(),
                            hashlib.sha256
                        ).hexdigest()

                        if hmac.compare_digest(received_sig, expected_sig):
                            # 验证通过：信任结果
                            chart_base64 = content
                        else:
                            # HMAC 不匹配：拒绝结果，可能是攻击尝试
                            _sandbox_logger.warning(
                                "Security: HMAC verification failed for chart result. "
                                "Possible tampering or forged output detected."
                            )
                            return {
                                "stdout": stdout if stdout.strip() else "Run completed with no output.",
                                "stderr": stderr + "\nSecurity: HMAC verification failed\n",
                                "error": True,
                                "chart_base64": None,
                            }
                    except Exception as e:
                        _sandbox_logger.warning("Failed to read chart result file: %s", e)

            # 安全机制：对 stdout/stderr 进行脱敏处理
            # 防止敏感信息（环境变量、密钥等）通过错误反馈链泄露给 LLM 服务商
            sanitized_stdout = _sanitize_output(stdout)
            sanitized_stderr = _sanitize_output(stderr)

            return {
                "stdout": sanitized_stdout if sanitized_stdout.strip() else "Run completed with no output.",
                "stderr": sanitized_stderr,
                "error": proc.returncode != 0,
                "chart_base64": chart_base64,
            }

        except Exception as exc:
            _sandbox_logger.debug("Sandbox launch failed: %s", exc, exc_info=True)
            # 异常信息也需要脱敏
            sanitized_error_msg = _sanitize_output(f"SandboxError: [{type(exc).__name__}] {exc}\n")
            return {
                "stdout": "",
                "stderr": sanitized_error_msg,
                "error": True,
                "chart_base64": None,
            }
        finally:
            # 清理临时文件 - 只清理预固定的安全路径
            try:
                os.unlink(code_path)
            except OSError:
                pass
            try:
                if os.path.exists(chart_result_file):
                    os.unlink(chart_result_file)
            except OSError:
                pass

    @staticmethod
    def _safe_serialize(variables: Dict[str, Any]) -> dict:
        """将变量序列化为 JSON 兼容的 dict，不可序列化的值转为字符串表示。"""
        result = {}
        for name, value in variables.items():
            try:
                json.dumps(value, ensure_ascii=False)
                result[name] = value
            except (TypeError, ValueError):
                result[name] = str(value)
        return result

    def get_environment_info(self) -> str:
        """描述当前沙箱环境信息，用于 prompt 构建。"""
        parts = [
            "Sandbox environment (subprocess-isolated):",
            f"  working_dir: {self.working_dir}",
            f"  font_path: {self._font_path or '(not found)'}",
            "  pre-configured: matplotlib(Agg backend), Chinese font (kt_font.ttf)",
            "  available libraries: pandas, numpy, matplotlib, seaborn",
        ]
        if self._variables:
            parts.append("  injected variables:")
            for k, v in self._variables.items():
                parts.append(f"    {k} = {v!r}")
        return "\n".join(parts)
