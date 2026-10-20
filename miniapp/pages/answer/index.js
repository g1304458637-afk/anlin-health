const {request,myResident,message,requireLogin}=require('../../utils/api');
Page({data:{questionnaire:{title:'正在加载...',questions:[]},answers:{},saving:false},
  async onLoad(options){if(!requireLogin())return;try{this.setData({questionnaire:await request('/api/questionnaires/'+Number(options.id))})}catch(e){message(e)}},
  selectOption(e){this.setData({['answers.'+e.currentTarget.dataset.qid]:e.currentTarget.dataset.value})},
  async submit(){const q=this.data.questionnaire;if(q.questions.some(item=>!this.data.answers[item.id])){wx.showToast({title:'请完成全部题目',icon:'none'});return}this.setData({saving:true});try{const r=await myResident();await request(`/api/residents/${r.id}/responses`,'POST',{questionnaire_id:q.id,answers:this.data.answers});wx.showModal({title:'问卷已记录',content:'您的回答已保存。该问卷不是疾病诊断结果。',showCancel:false,success:()=>wx.navigateBack()})}catch(e){message(e)}finally{this.setData({saving:false})}}
});
