const {request,myResident,message,requireLogin}=require('../../utils/api');
const types=[{type:'measure',title:'健康测量'},{type:'questionnaire',title:'完成问卷'},{type:'followup',title:'社区随访'}];
Page({data:{types,selected:0,title:'',dueDate:'',list:[],residentId:null},
  async onShow(){if(!requireLogin())return;try{const r=await myResident();this.setData({residentId:r.id});await this.load()}catch(e){message(e)}},
  async load(){const list=await request(`/api/residents/${this.data.residentId}/reminders`);this.setData({list:list.map(x=>({...x,type_name:types.find(t=>t.type===x.type)?.title||'健康事项'}))})},
  changeTitle(e){this.setData({title:e.detail.value})},chooseType(e){this.setData({selected:Number(e.detail.value)})},chooseDate(e){this.setData({dueDate:e.detail.value})},
  async save(){if(!this.data.title.trim()||!this.data.dueDate){wx.showToast({title:'请填写事项和日期',icon:'none'});return}try{await request(`/api/residents/${this.data.residentId}/reminders`,'POST',{title:this.data.title.trim(),type:types[this.data.selected].type,due_date:this.data.dueDate});this.setData({title:'',dueDate:''});await this.load();wx.showToast({title:'已添加'})}catch(e){message(e)}},
  async complete(e){try{await request(`/api/residents/${this.data.residentId}/reminders/${e.currentTarget.dataset.id}/complete`,'PATCH');await this.load()}catch(err){message(err)}}
});
