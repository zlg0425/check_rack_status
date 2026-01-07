"""
FOTA 相关模块
提供 FOTA 升级功能
"""

from core.fota.manager import (
    log_fota,
    load_fota_timing_stats,
    save_fota_timing_stats,
    record_fota_timing,
    detect_fota_port,
    get_avg_fota_timing,
    run_ucm_with_log,
    fota_timing_stats_lock,
)

__all__ = [
    'log_fota',
    'load_fota_timing_stats',
    'save_fota_timing_stats',
    'record_fota_timing',
    'detect_fota_port',
    'get_avg_fota_timing',
    'run_ucm_with_log',
    'fota_timing_stats_lock',
]
