const {apiBase} = require('../config');
function request(path, method='GET', data) {
  return new Promise((resolve,reject)=>{
    const token=wx.getStorageSync('access_token');
    wx.request({url: apiBase+path, method, data, timeout: 15000,
      header: {'Content-Type':'application/json', ...(token?{Authorization:`Bearer ${token}`}:{})},
      success(res){
        if(res.statusCode>=200 && res.statusCode<300){resolve(res.data);return}
        if(res.statusCode===401 && !path.startsWith('/api/auth/')){
          wx.removeStorageSync('access_token');
          wx.reLaunch({url:'/pages/login/index'});
        }
        let detail=res.data?.detail;
        if(typeof detail!=='string')detail=Array.isArray(detail)?detail.map(x=>x.msg).join('; '):'服务暂时不可用';
        const err=new Error(detail);err.statusCode=res.statusCode;reject(err);
      },
      fail(err){reject(new Error('网络连接失败。请检查服务地址和微信合法域名设置。'))}
    });
  });
}
function message(e){wx.showToast({title:e?.message||'操作失败',icon:'none',duration:2600})}
async function myResident(){return request('/api/residents/me')}
function requireLogin(){if(!wx.getStorageSync('access_token')){wx.reLaunch({url:'/pages/login/index'});return false}return true}
function fmtDate(iso){return iso?String(iso).slice(0,10):''}
module.exports={request,message,myResident,requireLogin,fmtDate};
