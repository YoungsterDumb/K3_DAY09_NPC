# Báo cáo cá nhân — Day 9: Multi-Agent E-commerce Dispute Resolution

## 1. Thông tin cá nhân

| Thông tin | Nội dung |
| --- | --- |
| Họ và tên | La Thế Quyền |
| MSSV | 2A202601699 |
| Khóa/Lớp | K3 |
| Vai trò chính | Python API pipeline, Policy hard-gate và Verifier |
| Ngày hoàn thành | 2026-08-05 |

## 2. Vai trò và phạm vi công việc

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao | Trạng thái |
| --- | --- | --- | --- | --- |
| API client và rate limit | `python_agent/api.py`, `python_agent/config.py` | `.env`, Groq API | JSON handoff, request ID, retry/cooldown | Hoàn thành, đã chạy API thật |
| Orchestration | `python_agent/pipeline.py` | 50 case JSON, Olist CSV | 50 output JSON, 300 trace event | Hoàn thành, đã chạy đủ 50 case |
| Business policy | `python_agent/policy.py` | Facts từ orders, items, payments | Issue, cause, party, refund, action | Hoàn thành, đã unit test sáu rule |
| Validation/package | `src/validate.js`, `scripts/package-output.ps1` | Thư mục `output/` | Kết quả hard-gate, `output.zip` | Hoàn thành, đã xác minh 50 JSON |
| Kiến trúc và metadata | `architecture.md`, `metadata.json`, `PYTHON_API.md` | Thiết kế và lượt chạy thật | Tài liệu kiến trúc/runtime | Hoàn thành |

## 3. Kết quả thực tế

- Triển khai sáu vai trò: Coordinator, Order & Seller, Payment, Delivery, Policy và Verifier.
- Sử dụng Groq OpenAI-compatible API với `llama-3.1-8b-instant`, quy mô 8B tham số, đáp ứng giới hạn không quá 10B.
- API key chỉ được đọc từ `.env`; `.env` và `.venv/` được loại khỏi Git.
- Xử lý đủ 50 input chính thức và tạo đúng 50 output theo schema.
- Trace thật có 300 event: mỗi agent/role xuất hiện đúng 50 lần.
- Phân bố kết quả: 8 `canceled_order_paid`, 8 `unavailable_order_paid`, 8 `late_delivery_seller`, 8 `late_delivery_logistics`, 9 `valid_split_payment` và 9 `unsupported_late_claim`.
- Tất cả 50 Verifier Agent event có `valid=true`; validator độc lập cũng chấp nhận đủ 50 output.
- `output.zip` được đóng gói chỉ từ `EC_001.json` đến `EC_050.json`.

## 4. Kiến trúc và luồng handoff

```text
50 input JSON + Olist CSV
           |
           v
   Coordinator Agent
      /     |      \
     v      v       v
 Order &  Payment  Delivery
 Seller    Agent    Agent
     \       |       /
      \------v------/
         Policy Agent
              |
       deterministic oracle
              |
        Coordinator build
              |
        Verifier Agent
              |
       output + trace + metadata
```

Coordinator dùng `claimed_order_id` để lọc và join `orders`, `order_items`, `order_payments`. Ba domain agent nhận đúng facts thuộc phạm vi của mình và trả JSON handoff có request ID thật. Policy Agent nhận cả facts và ba handoff để đề xuất issue/cause/action. Trước khi dựng output, deterministic `EC_POLICY_V1` oracle tính lại quyết định từ CSV; Verifier Agent sau đó audit candidate với expected policy. Cách tổ chức này giữ được trace multi-agent thật nhưng không cho nội dung LLM tự tạo ID, số tiền hoặc sự kiện ngoài dữ liệu.

## 5. Áp dụng chính sách và evidence

Policy oracle áp dụng đúng thứ tự ưu tiên:

1. `canceled_order_paid`.
2. `unavailable_order_paid`.
3. `late_delivery_seller`.
4. `late_delivery_logistics`.
5. `valid_split_payment`.
6. `unsupported_late_claim`.

Tiền được tính bằng `Decimal`, làm tròn hai chữ số với `ROUND_HALF_UP`; split payment được đối soát trong sai số 0.10 BRL. Payment total là tổng các payment row, không nhân với installment. Evidence chỉ được dựng từ khóa có thật theo năm dạng `order:`, `item:`, `payment:`, `seller:` và `policy:`.

## 6. Xác minh và kết quả lệnh chạy

```powershell
.\.venv\Scripts\python.exe -m unittest test.test_python_policy -v
.\.venv\Scripts\python.exe -m python_agent.pipeline --check-api
.\.venv\Scripts\python.exe -m python_agent.pipeline
node src\validate.js
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/package-output.ps1
```

Kết quả đã ghi nhận:

- Python unit test: sáu nhánh policy và bộ phân tích thời gian retry đều `OK`.
- API smoke test: `Processed 1 case(s), wrote 6 trace events with llama-3.1-8b-instant`.
- Lượt chạy chính thức: 50 output và 300 trace event.
- Validator: `Validated exactly 50 output files with complete schema and policy constraints`.
- Artifacts: `output/EC_001.json` đến `EC_050.json`, `trace.jsonl`, `metadata.json`, `output.zip`.

## 7. Blocker và cách xử lý

### 7.1. Groq Free Plan trả HTTP 429

- **Triệu chứng:** pipeline dừng sau case đầu do vượt giới hạn 6.000 token/phút.
- **Nguyên nhân:** ba domain request bắt đầu gần nhau và payload ban đầu chứa nhiều trường CSV không cần thiết.
- **Cách xử lý:** rút gọn payload theo nguyên tắc least-data, giãn request tối thiểu 5,1 giây, đọc `Retry-After`, áp dụng cooldown dùng chung và retry tối đa 10 lần.
- **Kết quả:** smoke test API chạy thành công; lượt 50 case hoàn thành trên Free Plan.

### 7.2. LLM 8B không tuân thủ tuyệt đối enum policy

- **Quan sát:** Policy Agent khớp `primary_issue` 34/50 case nhưng không trả đúng chuỗi enum `cause_code` và `action` trong 50/50 case.
- **Rủi ro:** nếu dùng trực tiếp câu trả lời LLM, output sẽ sai policy hoặc bị hard gate.
- **Cách xử lý:** coi LLM handoff là phân tích hỗ trợ; deterministic oracle là nguồn quyết định có thẩm quyền cho issue, cause, responsible party, refund và action. Mọi candidate tiếp tục qua Verifier Agent và validator độc lập.
- **Kết quả:** 50/50 output cuối hợp lệ, 50/50 Verifier event có `valid=true`; trace vẫn giữ nguyên phản hồi LLM thật để audit, không che giấu disagreement.

## 8. Quyết định kỹ thuật quan trọng

- Chọn model 8B vì phù hợp giới hạn đề bài và hỗ trợ JSON mode qua API.
- Không giao phép tính tiền hoặc dựng evidence cho LLM; các phần này cần tính xác định và truy nguyên được.
- Ghi model name trong source/metadata, nhưng chỉ lưu secret trong `.env`.
- Ghi đè `trace.jsonl` sau mỗi lượt chạy hoàn chỉnh để artifact chỉ phản ánh lượt mới nhất.
- Giữ validator độc lập với LLM để phát hiện lỗi schema, enum, cardinality, tiền, refund/status và evidence format.

## 9. Hiểu biết luồng end-to-end

1. Coordinator nhận case và truy xuất order thật bằng `claimed_order_id`.
2. Domain agents phân tích seller handoff, payment reconciliation và delivery timing.
3. Policy Agent nhận handoff và đề xuất quyết định theo `EC_POLICY_V1`.
4. Policy oracle tính lại từ facts gốc, chuẩn hóa các enum và số tiền.
5. Coordinator dựng affected entities, ranked cause, responsible parties, evidence và financial resolution.
6. Verifier Agent audit candidate; validator cục bộ thực hiện hard gate lần cuối.
7. Chỉ 50 JSON hợp lệ được đóng gói thành `output.zip`; trace và metadata được giữ ngoài zip theo yêu cầu.

## 10. Cam kết

- [x] Dùng model 8B, không vượt giới hạn 10B.
- [x] Trace là lượt chạy API thật của đủ 50 case, không append trace cũ.
- [x] Kết quả đã được kiểm tra bằng Verifier Agent và validator độc lập.
- [x] Không chứa API key, token hoặc secret trong source/artifact.
- [x] Báo cáo nêu rõ cả LLM disagreement và cơ chế hard-gate đã dùng.
- [x] Đã điền và kiểm tra họ tên, MSSV, đồng thời đổi đúng tên file.

**Họ và tên:** La Thế Quyền

**Ngày xác nhận:** 2026-08-05
