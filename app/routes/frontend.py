"""
前端路由模块
处理前端页面渲染
"""

from flask import Blueprint, render_template
import app.utils.config as config

bp = Blueprint('frontend', __name__)

@bp.route("/")
def index():
    """首页路由"""
    return render_template('index.html', ssh_user=config.ssh_username or "root")

