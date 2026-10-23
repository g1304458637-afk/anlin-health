# 虚构报告 OCR 功能测试样本

本目录的全部姓名、机构、日期和结果都是人为编造的功能测试内容，**不是病历、医学建议或 OCR 准确率标定集**。每页均标记“虚构测试样本 非真实医疗报告”。

- `fictional-two-page-report.pdf`：两页 A4 中文报告，文字可选择/复制；第 1 页含血压、血糖、体重及常见检验条目，第 2 页含生化及阴性定性条目。
- `fictional-first-page-scan.png`：由 PDF 第 1 页按固定参数栅格化并加轻微灰度扫描纹理，供图像输入路径测试；它本身没有可选文字。
- `expected.json`：逐条记录页码、项目名/代码、值、单位、参考范围和样本上印的字面标记，以及机构、姓名、采样/报告时间。`N`、`H`、`--` 只是虚构样本上的字符，不代表医学解释。

重建需要 `reportlab`、`Pillow` 和 Poppler 的 `pdftoppm`：

```bash
python3 backend/scripts/generate_report_samples.py
```

脚本默认使用当前用户目录中的 `~/Library/Fonts/Arial Unicode.ttf`，以确保中文可读且 PDF 文字可选择。其他机器可用 `REPORT_SAMPLE_FONT=/absolute/path/to/Chinese-capable.ttf` 指定支持中文的 TrueType 字体；更换字体可能改变 PDF 字节和排版。生成过程不联网、不读取应用数据库或密钥。PDF 使用固定元数据，扫描噪点使用固定随机种子，给定相同字体和工具版本时输出可复现。
