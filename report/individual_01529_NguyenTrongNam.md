# Member Role Report - Day 9: Multi Agent A2A

## 1. Thông tin cá nhân

| Thông tin | Nội dung |
| --- | --- |
| Họ và tên | Nguyễn Trọng Nam |
| MSSV | 2A202601529 |
| Khóa/Lớp | K3 |
| Vai trò chính | Thiết kế và triển khai pipeline Multi-Agent, policy engine, verifier, sinh output |
| Ngày hoàn thành | 2026-08-05 |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao | Trạng thái |
| --- | --- | --- | --- | --- |
| Multi-Agent orchestration | `src/run.js`, `src/agents/coordinator-agent.js` | 50 file case trong `input/` | Điều phối xử lý từng case và ghi kết quả | Hoàn thành |
| Data loading và join dữ liệu | `src/data-store.js`, `src/utils/csv.js` | `claimed_order_id`, các CSV Olist trong `data/` | Context gồm order, items, payments, sellers | Hoàn thành |
| Agent phân tích order/seller | `src/agents/order-seller-agent.js` | Order, item, seller rows | Trạng thái order, item total, freight total, seller giao trễ nếu có | Hoàn thành |
| Agent phân tích payment | `src/agents/payment-agent.js` | Payment rows và tổng item/freight | Tổng payment, split payment, trạng thái đối soát | Hoàn thành |
| Agent phân tích delivery | `src/agents/delivery-agent.js` | Ngày giao thực tế và ngày giao dự kiến | Kết luận giao trễ hoặc giao trong hạn | Hoàn thành |
| Policy và refund | `src/agents/policy-agent.js` | Handoff từ các specialist agents | Primary issue, root cause, responsible party, refund, action | Hoàn thành |
| Verifier và validation | `src/agents/verifier-agent.js`, `src/validate.js` | Output JSON và context gốc | Kết quả kiểm tra schema, evidence ID, giới hạn field | Hoàn thành |
| Tài liệu và audit | `architecture.md`, `trace.jsonl`, `metadata.json` | Kết quả chạy thực tế | Mô tả kiến trúc, trace, metadata model/runtime | Hoàn thành |

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động | Thành viên/module được hỗ trợ | Kết quả |
| --- | --- | --- |
| Kiểm tra API provider | OpenRouter/OpenAI model config | Xác nhận OpenRouter chạy được với `qwen/qwen-2.5-7b-instruct`; OpenAI đã thử nhưng quay lại OpenRouter để đảm bảo model có parameter size rõ ràng <= 7B |
| Đóng gói output | Submission artifact | Tạo `output.zip` chỉ chứa 50 JSON từ `EC_001.json` đến `EC_050.json` |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao | Cách xác minh |
| --- | --- | --- | --- |
| Sinh output cho toàn bộ case | `output/EC_001.json` đến `output/EC_050.json` | Đủ 50 file JSON theo schema README | `npm.cmd run validate` |
| Điều phối luồng agent | `src/agents/coordinator-agent.js` | Mỗi case có handoff giữa Coordinator, OrderSeller, Payment, Delivery, Policy, Verifier | Kiểm tra `trace.jsonl` |
| Áp dụng policy `EC_POLICY_V1` | `src/agents/policy-agent.js` | Phân loại 6 nhóm issue và tính refund tương ứng | `npm.cmd run run` |
| Kiểm chứng output | `src/agents/verifier-agent.js` | Không có evidence ID bịa, không vượt limit schema | `npm.cmd run validate` |
| Ghi metadata model/runtime | `metadata.json` | Provider OpenRouter, model Qwen 7B, framework Node.js | Đọc `metadata.json` |

Một output cụ thể mà phần việc tạo ra là thư mục `output/` với đủ 50 file JSON. Mỗi file có `assessment`, `affected_entities`, `root_cause_analysis`, `evidence_ids`, `financial_resolution` và `resolution_actions`. Lần chạy cuối đã validate thành công 50/50 case.

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

Bài toán yêu cầu xử lý khiếu nại thương mại điện tử dựa trên nhiều nguồn dữ liệu khác nhau. Một phản ánh như "giao hàng trễ" không thể kết luận chỉ từ lời khách hàng, mà phải đối chiếu trạng thái đơn, ngày seller bàn giao, ngày carrier nhận hàng, ngày giao thực tế, tổng giá trị item, freight và payment.

Phần tôi phụ trách là biến quy trình này thành một pipeline Multi-Agent có handoff rõ ràng, kết quả có thể kiểm chứng và sinh được đúng 50 output JSON để nộp.

### Cách triển khai

Hệ thống được triển khai bằng Node.js với các agent tách vai trò:

- `CoordinatorAgent` đọc input case, lấy `claimed_order_id`, gọi data store join dữ liệu và điều phối các agent.
- `OrderSellerAgent` kiểm tra `order_status`, danh sách item, seller, tổng item/freight và seller nào giao cho carrier sau `shipping_limit_date`.
- `PaymentAgent` tính tổng payment, phát hiện split payment và đối soát tổng payment với `item_total + freight_total` trong sai số 0.10 BRL.
- `DeliveryAgent` so sánh `order_delivered_customer_date` với `order_estimated_delivery_date`.
- `PolicyAgent` áp dụng đúng thứ tự ưu tiên của `EC_POLICY_V1`.
- `VerifierAgent` kiểm tra output cuối cùng trước khi ghi file.

Các phép tính tiền được xử lý ở đơn vị cent rồi mới chuyển về BRL để tránh lỗi số thực. Các ID nhiều dòng như item/payment được sort ổn định theo `order_item_id` và `payment_sequential` để output dễ khớp ground truth hơn.

### Input, output và contract

| Thành phần | Mô tả |
| --- | --- |
| Input | `input/EC_*.json`, trong đó mỗi file có `case_id`, `opened_at`, `customer_request.claimed_order_id`, `policy_version` |
| Dữ liệu phụ thuộc | `data/olist_orders_dataset.csv`, `data/olist_order_items_dataset.csv`, `data/olist_order_payments_dataset.csv`, `data/olist_sellers_dataset.csv` |
| Output | `output/EC_*.json`, `trace.jsonl`, `metadata.json`, `output.zip` |
| Module phụ thuộc | `src/data-store.js`, `src/utils/csv.js`, `src/utils/money.js`, `src/utils/time.js` |
| Module sử dụng output | Grader/chấm điểm, `src/validate.js` |
| Điều kiện lỗi cần xử lý | Order không có item row, canceled/unavailable có payment nhưng không có item, nhiều payment row, giao trễ do seller hoặc logistics, evidence ID sai định dạng |

### Cách xác minh

```bash
npm.cmd run test:api
npm.cmd run run
npm.cmd run validate
```

- Kết quả mong đợi: API key dùng được, pipeline xử lý đủ 50 case, output đúng schema và evidence hợp lệ.
- Kết quả thực tế: OpenRouter API check thành công, `Processed 50/50 cases`, `Validated 50 output files successfully`.
- Artifact/log: `output/`, `output.zip`, `trace.jsonl`, `metadata.json`.

## 5. Một quyết định kỹ thuật quan trọng

- Bối cảnh: Bài yêu cầu kiến trúc Multi-Agent và model không quá 10B parameters, nhưng output lại được chấm bằng schema/ground truth nên cần độ ổn định cao.
- Các phương án đã cân nhắc: dùng LLM quyết định trực tiếp từng case; dùng deterministic rule engine nhưng tổ chức theo các agent có handoff; hoặc dùng framework agent nặng như LangGraph.
- Phương án đã chọn: dùng Node.js lightweight multi-agent orchestrator, mỗi agent là một module độc lập có input/output rõ ràng; model khai báo là `qwen/qwen-2.5-7b-instruct` qua OpenRouter.
- Lý do: Qwen 2.5 7B đáp ứng ràng buộc <=10B rõ ràng hơn các model GPT không công bố parameter count. Phần quyết định chính vẫn deterministic theo CSV và policy để tránh hallucination, dễ tái lập và dễ debug.
- Bằng chứng quyết định phù hợp: `npm.cmd run test:api` pass với OpenRouter, `npm.cmd run run` xử lý đủ 50 case, `npm.cmd run validate` pass 50/50. `metadata.json` ghi đúng provider, model, parameter size và runtime.

## 6. Một lỗi hoặc blocker đã xử lý

- Triệu chứng/lỗi nguyên văn: `npm : File D:\Node\npm.ps1 cannot be loaded because running scripts is disabled on this system.`
- Lệnh hoặc bước tái hiện: chạy `npm run run` trong PowerShell.
- Nguyên nhân gốc: Windows PowerShell execution policy chặn file script `npm.ps1`.
- Cách xử lý: dùng `npm.cmd run run`, `npm.cmd run validate` và `npm.cmd run test:api` thay cho `npm run ...`.
- Cách xác minh sau khi sửa: pipeline chạy thành công, xử lý 50/50 case và validate pass.
- Điều học được: Khi làm lab trên Windows, nên ghi rõ lệnh `npm.cmd` để tránh phụ thuộc vào policy của PowerShell.

Một cải thiện khác đã xử lý là thứ tự `payment_ids`. Ban đầu payment trong CSV có thể xuất hiện `:2` trước `:1`, dễ lệch với ground truth dù dữ liệu đúng. Tôi đã sort payment theo `payment_sequential`, item theo `order_item_id` để output ổn định hơn.

## 7. Hiểu biết về luồng end-to-end

1. Dữ liệu đi từ input case đến output như sau: `input/EC_*.json` cung cấp `claimed_order_id`, data store dùng ID này để join order, items, payments và sellers từ CSV, sau đó các agent phân tích từng phần và PolicyAgent đưa ra quyết định cuối.
2. Evaluation set là 50 file `EC_001.json` đến `EC_050.json`. Ground truth có thể được suy ra từ dữ liệu Olist và bảng rule `EC_POLICY_V1` trong README, gồm primary issue, affected entities, root cause, evidence, refund và action.
3. Quality checks trong bài này tập trung vào tính đúng của output hiện tại: schema, giới hạn số lượng field, evidence ID có tồn tại, tính tiền và action. Đây không phải freshness monitoring vì dữ liệu là snapshot CSV cố định.
4. Phải dùng cùng test set cho mọi lần chạy để so sánh công bằng. Nếu đổi tập case hoặc đổi dữ liệu, không thể biết điểm tăng/giảm do thuật toán tốt hơn hay do case dễ hơn.
5. Một lần repair được xem là thành công khi `output/` có đúng 50 JSON, `npm.cmd run validate` pass, `trace.jsonl` ghi lại handoff thật của các agent, `metadata.json` ghi đúng model/runtime và artifact nộp `output.zip` không chứa source code hoặc secret.

## 8. Cam kết của thành viên

- [x] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [x] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [x] Tôi không ghi "đã chạy thành công" cho phần chưa được kiểm chứng.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

Họ và tên: Nguyễn Trọng Nam

Ngày xác nhận: 2026-08-05
