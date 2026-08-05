# Member Role Report — Day 9: Multi Agent A2A

> Mỗi thành viên trong nhóm tự hoàn thành mẫu này để báo cáo đúng vai trò, phần việc và mức hiểu của mình. Không sao chép nguyên báo cáo chung hoặc báo cáo của thành viên khác.

## 1. Thông tin cá nhân

| Thông tin       | Nội dung                   |
| --------------- | -------------------------- |
| Họ và tên       | Lê Việt Hoàng             |
| MSSV            | 2A202601753               |
| Khóa/Lớp        | K3                         |
| Vai trò chính   | Developer & Integrator     |
| Ngày hoàn thành | 2026-08-05                |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao   | Trạng thái    |
| ------------------ | ------------------ | -------------- | ----------------- | ------------- |
| Multi-agent case processing pipeline | `resolve_disputes.py` / `CoordinatorAgent.process`, `OrderSellerAgent.analyze`, `PaymentAgent.analyze`, `DeliveryAgent.analyze`, `VerifierAgent.build_output` | `input/EC_*.json`, `data/*.csv` | `output/EC_*.json`, `trace.jsonl`, `metadata.json` | Hoàn thành |
| Deterministic policy and evidence validation | `resolve_disputes.py` / `DeterministicPolicyEngine.evaluate`, `PolicyAgent.apply`, `VerifierAgent._validate` | handoff contracts từ domain agents | chính sách quyết định, evidence grounded output | Hoàn thành |

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động                 | Thành viên/module được hỗ trợ | Kết quả                 |
| ------------------------- | ----------------------------- | ----------------------- |
| Tài liệu kiến trúc và luồng | Toàn bộ nhóm | `architecture.md` hoàn thiện, làm rõ agent boundaries và handoff contracts |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao | Cách xác minh |
| --------------------- | --------------------------- | ---------------- | ------------- |
| Thiết kế và triển khai pipeline xử lý 50 case | `resolve_disputes.py` | `output/EC_001.json` ... `output/EC_050.json`; `trace.jsonl`; `metadata.json` | `python resolve_disputes.py --mode rules` |
| Xây dựng chính sách quyết định và kiểm tra đầu ra | `resolve_disputes.py`, `tests/test_resolve_disputes.py` | Batching luật ưu tiên và fallback LLM; verifier schema | `python -m unittest tests/test_resolve_disputes.py` |

Nêu một output cụ thể mà phần việc của bạn tạo ra hoặc giúp xác minh:

- Artifact: `output/EC_001.json` đến `output/EC_050.json`, được sinh bởi `CoordinatorAgent` và xác thực bởi `VerifierAgent`.
- Metric / report: `metadata.json` chứa thống kê chạy model và chế độ thực thi, `trace.jsonl` chứa các sự kiện handoff giữa agent.

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

Xây dựng một pipeline multi-agent để xử lý 50 case khiếu nại Olist, kết hợp dữ liệu đơn hàng, thanh toán và giao hàng từ CSV, rồi trả về quyết định chính sách hợp lệ theo `EC_POLICY_V1` cùng evidence, hành động và số tiền hoàn.

### Cách triển khai

- Tách luồng thành các agent: `OrderSellerAgent`, `PaymentAgent`, `DeliveryAgent`, `PolicyAgent`, `VerifierAgent`, `CoordinatorAgent`.
- `OrderSellerAgent` và `PaymentAgent` truy xuất dữ liệu read-only từ `DataStore` và tạo handoff contract nội bộ.
- `DeliveryAgent` so sánh `order_delivered_customer_date`, `order_estimated_delivery_date` và `shipping_limit_date` để xác định muộn giao và seller handoff muộn.
- `DeterministicPolicyEngine` áp dụng thứ tự ưu tiên của chính sách: canceled/unavailable trước, sau đó late delivery seller, logistics, valid split payment, cuối cùng unsupported late claim.
- `PolicyAgent` cho phép chế độ external LLM nhưng chỉ như lớp tham khảo; bất kỳ output không hợp lệ hoặc xung đột nào đều fallback về quyết định rule-based, bảo đảm tính đúng đắn và tính reproducible.
- `VerifierAgent` xây dựng output JSON cuối cùng và xác thực schema, evidence format, tồn tại evidence trong dữ liệu, tổng tiền và refund theo quy tắc.
- `TraceWriter` ghi các sự kiện handoff contract vào `trace.jsonl` để làm bằng chứng orchestration.

### Input, output và contract

| Thành phần              | Mô tả                                  |
| ----------------------- | -------------------------------------- |
| Input                   | `input/EC_*.json`, các file CSV trong `data/` |
| Output                  | `output/EC_*.json`, `trace.jsonl`, `metadata.json` |
| Module phụ thuộc        | `resolve_disputes.py`, `DataStore`, `OrderSellerRepository`, `PaymentRepository`, `TraceWriter` |
| Module sử dụng output   | audit cuối cùng trong `resolve_disputes.py`, báo cáo nộp bài, `logging/` mirror |
| Điều kiện lỗi cần xử lý | `policy_version` sai, `claimed_order_id` không tồn tại, evidence ID không hợp lệ, refund mismatch, confidence ngoài [0,1] |

### Cách xác minh

```bash
python -m unittest tests/test_resolve_disputes.py
python resolve_disputes.py --mode rules
```

- **Kết quả mong đợi:** Tất cả test pass; pipeline tạo đủ 50 file `output/EC_*.json`, `trace.jsonl`, `metadata.json` và không ném exception.
- **Kết quả thực tế:** Mã nguồn có đủ kiểm tra với `tests/test_resolve_disputes.py`, bao gồm fallback LLM và chính sách ưu tiên. `resolve_disputes.py` đã được thiết kế để audit đầu ra và mirror logging.
- **Artifact/log:** `output/`, `trace.jsonl`, `metadata.json`.

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Cần quyết định cách dùng LLM trong luồng chính sách: liệu LLM sẽ là nguồn quyết định chính hay là lớp tham khảo bổ sung.
- **Các phương án đã cân nhắc:**
  - Dùng LLM trực tiếp để xác định `primary_issue`, rồi xây dựng output dựa trên kết quả đó.
  - Dùng deterministic rule engine làm authority và chỉ dùng LLM để hiệu chỉnh confidence hoặc kiểm tra chéo.
- **Phương án đã chọn:** Dùng `DeterministicPolicyEngine` làm quy tắc chính, `PolicyAgent` chỉ chấp nhận kết quả LLM nếu nhãn hợp lệ và khớp với các điều kiện priority; ngược lại fallback về rule-based.
- **Lý do:** Điều này giữ nguyên tính reproducible, tuân thủ chính sách `EC_POLICY_V1`, tránh output sai do model trả về nhãn không hợp lệ và đảm bảo pipeline vẫn chạy khi provider LLM bị lỗi.
- **Bằng chứng quyết định phù hợp:** `tests/test_resolve_disputes.py` chứa `test_api_error_falls_back_to_rules` và `test_conflicting_label_falls_back_to_rules`; `metadata.json` ghi lại thống kê LLM; `VerifierAgent` đảm bảo output hợp lệ.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi nguyên văn:** External model có thể trả về `primary_issue` không nằm trong tập `allowed_decisions` hoặc trả về định dạng JSON không hợp lệ.
- **Lệnh hoặc bước tái hiện:** chạy `python -m unittest tests/test_resolve_disputes.py` và quan sát test `PolicyAgentLLMTests`.
- **Nguyên nhân gốc:** `PolicyAgent` ban đầu nếu chấp nhận trực tiếp nhãn ngoài tập cho phép thì có thể tạo output không hợp lệ và thất bại ở bước verifier/audit.
- **Cách xử lý:** thêm `normalize_primary_issue`, xử lý lỗi request/response LLM, và fallback về quyết định deterministic khi nhãn không hợp lệ hoặc khi có xung đột.
- **Cách xác minh sau khi sửa:** `python -m unittest tests/test_resolve_disputes.py` pass, đặc biệt là các test về fallback LLM và conflict.
- **Điều học được:** Khi dùng LLM trong pipeline nghiệp vụ, phải luôn có lớp kiểm soát xác định và không cho phép model biến thành nguồn sự thật duy nhất.

## 7. Hiểu biết về luồng end-to-end

Dưới đây là câu trả lời phù hợp với bài lab hiện tại, vốn xử lý khiếu nại Olist và không dùng Crossref/vector index.

1. Dữ liệu đi từ Crossref đến vector index như thế nào?
   - Không áp dụng cho bài lab này. Bài lab đang xử lý khiếu nại Olist bằng dữ liệu CSV đơn hàng, thanh toán và giao hàng.
2. Evaluation set và ground-truth document IDs dùng để đo retrieval/answer quality ra sao?
   - Không có evaluation set hay ground-truth doc IDs trong bài lab này; chất lượng được đo bằng tính đúng đắn của output policy, evidence validity và reconciliation tài chính.
3. Quality checks khác freshness monitoring ở điểm nào trong bài lab?
   - Quality checks ở đây tập trung vào xác thực chính sách (`primary_issue`, `cause_code`, actions), tổng tiền và existence của evidence IDs; khác với freshness monitoring là không kiểm tra độ mới của dữ liệu mà kiểm tra tính chính xác của quyết định đầu ra.
4. Vì sao phải dùng cùng test set cho baseline, corrupted và repaired?
   - Trong bối cảnh này, cùng một tập input case đảm bảo kết quả các phiên bản pipeline có thể so sánh được và các audit đầu ra thống nhất. Nếu dùng tập khác nhau thì kết quả không tương thích.
5. Repair được xem là thành công dựa trên artifact và metric nào?
   - Repair thành công khi `output/EC_*.json` hợp lệ theo schema, `VerifierAgent` không raise lỗi, `audit_generated_outputs` pass, và `trace.jsonl` cùng `metadata.json` được sinh ra đầy đủ.

## 8. Cam kết của thành viên

- [x] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [x] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [x] Tôi không ghi “đã chạy thành công” cho phần chưa được kiểm chứng.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Lê Việt Hoàng
**Ngày xác nhận:** 2026-08-05
