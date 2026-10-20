const {request,message,requireLogin}=require('../../utils/api');
Page({data:{list:[]},async onShow(){if(!requireLogin())return;try{this.setData({list:await request('/api/questionnaires')})}catch(e){message(e)}},open(e){wx.navigateTo({url:'/pages/answer/index?id='+e.currentTarget.dataset.id})}});
