// 开发者工具预览本机后端：127.0.0.1:8000；真机预览不可访问电脑 localhost。
// 上线前改成 HTTPS + 已配置微信 request 合法域名；不得把 WECHAT_APPSECRET 写在这里。
// enableDemoLogin 控制登录页“本地开发演示登录”入口，正式发布必须改为 false。
module.exports = { apiBase: 'http://127.0.0.1:8000', enableDemoLogin: true };
