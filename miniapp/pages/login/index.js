const {request,message}=require('../../utils/api');
const {enableDemoLogin}=require('../../config');
Page({data:{username:'resident',password:'DemoPass2026!',loading:false,demoEnabled:enableDemoLogin===true},
  onUsername(e){this.setData({username:e.detail.value})},onPassword(e){this.setData({password:e.detail.value})},
  async signedIn(response){wx.setStorageSync('access_token',response.access_token);getApp().globalData.user=response.user;wx.reLaunch({url:response.user.role==='family'?'/pages/family/index':'/pages/home/index'})},
  async demoLogin(){this.setData({loading:true});try{const r=await request('/api/auth/dev-login','POST',{username:this.data.username,password:this.data.password});await this.signedIn(r)}catch(e){message(e)}finally{this.setData({loading:false})}},
  wechatLogin(){this.setData({loading:true});wx.login({success:async r=>{try{if(!r.code)throw new Error('微信暂未返回登录凭证');const out=await request('/api/auth/wechat','POST',{code:r.code});await this.signedIn(out)}catch(e){message(e)}finally{this.setData({loading:false})}},fail:()=>{this.setData({loading:false});message(new Error('微信登录未完成'))}})}
});
