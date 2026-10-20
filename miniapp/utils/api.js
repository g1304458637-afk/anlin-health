const {apiBase} = require('../config');
// 防重复提交：写操作在途时拦截同键重复请求；GET 网络失败自动重试一次。
const inflight = new Set();

function request(path, method = 'GET', data, _retried = false) {
  const isWrite = method !== 'GET';
  const key = method + ' ' + path + ' ' + (data ? JSON.stringify(data) : '');
  if (isWrite && inflight.has(key)) {
    return Promise.reject(new Error('正在保存，请勿重复提交'));
  }
  return new Promise((resolve, reject) => {
    const token = wx.getStorageSync('access_token');
    if (isWrite) inflight.add(key);
    wx.request({url: apiBase + path, method, data, timeout: 15000,
      header: {'Content-Type': 'application/json', ...(token ? {Authorization: `Bearer ${token}`} : {})},
      complete() { if (isWrite) inflight.delete(key); },
      success(res) {
        if (res.statusCode >= 200 && res.statusCode < 300) { resolve(res.data); return }
        if (res.statusCode === 401 && !path.startsWith('/api/auth/')) {
          wx.removeStorageSync('access_token');
          wx.reLaunch({url: '/pages/login/index'});
        }
        let detail = res.data?.detail;
        if (typeof detail !== 'string') detail = Array.isArray(detail) ? detail.map(x => x.msg).join('; ') : '服务暂时不可用';
        const err = new Error(detail); err.statusCode = res.statusCode; reject(err);
      },
      fail() {
        // 弱网下只对读取类请求做一次重试；写请求由用户确认后重发，避免重复建档。
        if (!isWrite && !_retried) {
          setTimeout(() => resolve(request(path, method, data, true)), 1200);
          return;
        }
        reject(new Error('网络连接失败。请检查网络和微信合法域名设置。'));
      }
    });
  });
}
function message(e) { wx.showToast({title: e?.message || '操作失败', icon: 'none', duration: 2600}) }
async function myResident() { return request('/api/residents/me') }
function requireLogin() { if (!wx.getStorageSync('access_token')) { wx.reLaunch({url: '/pages/login/index'}); return false } return true }
function logout() { wx.removeStorageSync('access_token'); getApp().globalData.user = null; wx.reLaunch({url: '/pages/login/index'}) }
function fmtDate(iso) { return iso ? String(iso).slice(0, 10) : '' }
module.exports = {request, message, myResident, requireLogin, logout, fmtDate};
