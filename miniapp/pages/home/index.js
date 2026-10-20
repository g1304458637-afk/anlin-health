const {request,message,requireLogin,myResident}=require('../../utils/api');
Page({data:{user:{},resident:null},async onShow(){if(!requireLogin())return;try{const user=await request('/api/me');if(user.role==='family'){wx.redirectTo({url:'/pages/family/index'});return}this.setData({user});try{this.setData({resident:await myResident()})}catch(e){if(e.statusCode===404)this.setData({resident:null});else throw e}}catch(e){message(e)}},
  goProfile(){wx.navigateTo({url:'/pages/profile/index'})},goVitals(){wx.navigateTo({url:'/pages/vitals/index'})},
  goQuestionnaires(){wx.navigateTo({url:'/pages/questionnaires/index'})},goReminders(){wx.navigateTo({url:'/pages/reminders/index'})},
  goFamily(){wx.navigateTo({url:'/pages/family/index'})},logout(){wx.removeStorageSync('access_token');wx.reLaunch({url:'/pages/login/index'})}
});
