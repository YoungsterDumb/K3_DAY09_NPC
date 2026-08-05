# Báo cáo cá nhân — K3 Day 09: Multi-Agent E-commerce Dispute Resolution

## 1. Thông tin cá nhân

| Thông tin       | Nội dung                                         |
| --------------- | ------------------------------------------------ |
| Họ và tên       | Lê Quốc An                                       |
| MSSV            | 2A202601811                                      |
| Khóa/Lớp        | K3 / D304                                        |
| Vai trò chính   | Thiết kế pipeline multi-agent và tích hợp output |
| Ngày hoàn thành | 2026-08-05                                       |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable                  | File/hàm phụ trách                                  | Input                            | Output                                          | Trạng thái |
| ----------------------------------- | --------------------------------------------------- | -------------------------------- | ----------------------------------------------- | ---------- |
| Điều phối multi-agent               | `multi_agent_system.py` / `CoordinatorAgent.run`    | Case JSON                        | Output JSON theo schema                         | Hoàn thành |
| Phân tích order, seller và delivery | `OrderSellerAgent`, `DeliveryAgent`                 | Orders, order items, seller CSV  | Handoff về trạng thái, seller và thời gian giao | Hoàn thành |
| Đối soát thanh toán                 | `PaymentAgent`                                      | Payment CSV và tổng item/freight | Handoff reconciliation                          | Hoàn thành |
| Áp dụng policy                      | `PolicyAgent.run`                                   | Các handoff domain               | Issue, root cause, refund và action             | Hoàn thành |
| Kiểm chứng output                   | `VerifierAgent.verify`                              | Output cuối và CSV keys          | Output hợp lệ hoặc lỗi kiểm chứng               | Hoàn thành |
| Chạy 50 case và đóng gói            | `run_pipeline.py`, `run_pipeline.ps1`, `output.zip` | `input/EC_*.json`                | 50 JSON và ZIP nộp bài                          | Hoàn thành |

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động                 | Thành phần được hỗ trợ  | Kết quả                                                  |
| ------------------------- | ----------------------- | -------------------------------------------------------- |
| Debug evidence validation | `multi_agent_system.py` | Sửa lỗi không được tạo `seller:*` cho platform/logistics |
| Kiểm tra cấu trúc ZIP     | `output.zip`            | ZIP có đúng 50 entry dưới thư mục `output/`              |

## 3. Kết quả theo vai trò

| Nhiệm vụ                        | Artifact liên quan                            | Kết quả                                                                                     | Cách xác minh                            |
| ------------------------------- | --------------------------------------------- | ------------------------------------------------------------------------------------------- | ---------------------------------------- |
| Xây dựng agent graph và handoff | `multi_agent_system.py`                       | Coordinator gọi 5 agent domain theo thứ tự                                                  | Đọc `CoordinatorAgent.run`               |
| Áp dụng thứ tự ưu tiên policy   | `PolicyAgent.run`                             | Xử lý canceled/unavailable, seller late, logistics late, split payment và unsupported claim | Đối chiếu README và source               |
| Sinh output cho bộ input        | `output/EC_001.json` ... `output/EC_050.json` | 50 JSON được sinh                                                                           | `OUTPUT_COUNT=50`                        |
| Kiểm tra output                 | `validate_outputs.py` và kiểm tra PowerShell  | `INVALID_COUNT=0`                                                                           | Parse JSON, confidence và evidence limit |
| Đóng gói nộp bài                | `output.zip`                                  | 50 file theo dạng `output/EC_*.json`                                                        | `ZIP_ENTRY_COUNT=50`                     |

Artifact chính do phần việc tạo ra là [output.zip](output.zip), chứa đúng 50 kết quả theo cấu trúc mà hệ thống chấm yêu cầu.

## 4. Giải thích phần kỹ thuật

### Vấn đề cần giải quyết

Một claim giao hàng không thể được quyết định chỉ bằng message của khách hàng. Pipeline cần join order, order items, seller handoff, delivery timestamps và payments; sau đó áp dụng policy có thứ tự ưu tiên và chỉ sử dụng evidence ID tồn tại trong dữ liệu Olist.

### Cách triển khai

`CsvStore` tạo các index đọc-only theo `order_id` và `seller_id`. Coordinator nhận `claimed_order_id`, sau đó thực hiện các handoff:

1. `OrderSellerAgent` lấy order/items, tính item và freight total, đồng thời xác định seller giao cho carrier sau `shipping_limit_date`.
2. `PaymentAgent` cộng toàn bộ `payment_value`, đối chiếu với item total + freight trong sai số `0.10 BRL`, và nhận diện split payment.
3. `DeliveryAgent` so sánh ngày giao khách hàng với estimated delivery date.
4. `PolicyAgent` áp dụng đúng thứ tự ưu tiên trong `EC_POLICY_V1`.
5. `VerifierAgent` kiểm tra confidence, giới hạn số ID/evidence và tính hợp lệ của evidence ID.

Các giá trị business rule được khai báo trong source vì chúng là policy cố định của bài lab; order ID, seller ID, payment ID và số tiền đều được đọc động từ input/CSV.

### Input, output và contract

| Thành phần            | Mô tả                                                                                                               |
| --------------------- | ------------------------------------------------------------------------------------------------------------------- |
| Input                 | `input/EC_001.json` đến `input/EC_050.json` và 9 CSV trong `data/`                                                  |
| Output                | Một JSON tương ứng trong `output/`, gồm assessment, entities, root cause, evidence, financial resolution và actions |
| Module phụ thuộc      | `CsvStore`, các agent domain và `PolicyAgent`                                                                       |
| Module sử dụng output | `VerifierAgent`, `validate_outputs.py`, file ZIP nộp bài                                                            |
| Điều kiện lỗi         | Policy version không hỗ trợ, evidence không tồn tại, confidence ngoài `[0,1]`, quá giới hạn schema                  |

### Cách xác minh

```powershell
python run_pipeline.py
python validate_outputs.py
Compress-Archive -Path output\EC_*.json -DestinationPath output.zip -Force
```

Kết quả đã xác minh:

- Có đủ 50 input case.
- Có đủ 50 output JSON.
- Các output parse được và không có confidence/evidence vượt giới hạn.
- `output.zip` chứa đúng 50 file với đường dẫn `output/EC_001.json` đến `output/EC_050.json`.

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Bài lab yêu cầu nhiều agent có phân công và handoff, nhưng dữ liệu và policy có cấu trúc rõ ràng.
- **Phương án cân nhắc:** Dùng một prompt LLM duy nhất; hoặc dùng các agent deterministic độc lập theo từng domain.
- **Phương án đã chọn:** Các agent deterministic với contract handoff rõ ràng.
- **Lý do:** Kết quả reproducible, không bịa event ngoài CSV, dễ kiểm tra evidence và phù hợp với các rule định lượng của Olist.
- **Bằng chứng:** 50 output được sinh từ cùng một pipeline; evidence được kiểm tra theo các key thật trong CSV.

## 6. Một lỗi đã xử lý

- **Triệu chứng:** `VerifierAgent` báo `AssertionError: invalid evidence id`.
- **Nguyên nhân gốc:** Coordinator tạo `seller:OLIST_PLATFORM` hoặc `seller:LOGISTICS_PROVIDER`, dù đây không phải seller ID trong CSV.
- **Cách xử lý:** Chỉ thêm `seller:<id>` khi party ID tồn tại trong tập seller IDs của Olist.
- **Cách xác minh sau khi sửa:** Đối chiếu toàn bộ evidence của 50 output với các order, item, payment, seller và policy IDs hợp lệ; không còn evidence không hợp lệ.
- **Bài học:** Evidence phải được tạo từ dữ liệu đã xác thực, không chỉ từ tên party trong quyết định policy.

## 7. Hiểu biết về luồng end-to-end

Dữ liệu đi từ case JSON đến Coordinator, rồi được phân tích song domain qua các handoff order/seller, payment và delivery. Policy Agent tổng hợp các handoff để chọn một primary issue duy nhất; Verifier kiểm tra contract trước khi ghi output.

Evaluation không dựa trên retrieval hay Crossref. Bộ input 50 case được xử lý bằng cùng một policy và cùng cách tính cho mọi case, giúp kết quả reproducible. Quality checks nằm ở Verifier, validator structural và kiểm tra ZIP. Thành công của pipeline được thể hiện bằng 50 JSON hợp lệ, evidence ID có thật, số tiền đúng rule và cấu trúc ZIP đúng yêu cầu chấm.

## 8. Cam kết của thành viên

- [x] Nội dung báo cáo phản ánh đúng phần việc và artifact đã thực hiện.
- [x] Có thể giải thích luồng end-to-end, không chỉ module phụ trách.
- [x] Không ghi nhận thành công cho bước chưa được kiểm chứng.
- [x] Báo cáo không chứa API key, token hoặc secret.
- [x] Báo cáo không sao chép nguyên văn báo cáo của thành viên khác.

**Họ và tên:** Lê Quốc An  
**MSSV:** 01811  
**Lớp:** D304  
**Ngày xác nhận:** 2026-08-05
