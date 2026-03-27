# Hướng Dẫn Sử Dụng Bot Discord

## Mục lục
- [Giải trí](#giải-trí)
- [Game Quiz & Hát](#game-quiz--hát)
- [Thời tiết](#thời-tiết)
- [Đếm ngược / Kỳ thi](#đếm-ngược--kỳ-thi)
- [Tư vấn](#tư-vấn)
- [Sự kiện](#sự-kiện)
- [Hỗ trợ & Gọi Mod](#hỗ-trợ--gọi-mod)
- [Quản lý (Mod/Admin)](#quản-lý-modadmin)
- [Tiện ích khác](#tiện-ích-khác)
- [Tính năng tự động](#tính-năng-tự-động)

---

## Giải trí

| Lệnh | Mô tả |
|---|---|
| `!hello` | Bot chào bạn |
| `!lovecalc @user1 @user2` | Tính % tình yêu giữa 2 người (aliases: `!love`, `!tinhyeu`) |

---

## Game Quiz & Hát

| Lệnh | Mô tả |
|---|---|
| `!superquiz` | Bắt đầu Super Quiz |
| `!stopsuperquiz` | Dừng Super Quiz |
| `!skip` | Bỏ qua câu hỏi hiện tại |
| `!check` | Xem điểm cá nhân (aliases: `!diem`, `!score`) |
| `!bxh` | Xem bảng xếp hạng (aliases: `!leaderboard`, `!top`, `!rank`) |
| `!quizprogress` | Xem tiến độ quiz (aliases: `!progress`, `!tiendo`) |
| `!khoga` | Xem kho gà / tủ đồ (aliases: `!mykhoga`, `!inventory`, `!tuido`) |
| `!resetquiz1` | Reset quiz của bản thân |
| `!time` | Bắt đầu bộ đếm thời gian |
| `!stoptime` | Dừng bộ đếm thời gian |
| `!sing` | Chơi game hát cùng bot (alias: `!hat`) |
| `!stopsing` | Dừng game hát (alias: `!stophat`) |

---

## Thời tiết

| Lệnh | Mô tả |
|---|---|
| `!weather <thành phố>` | Xem thời tiết hiện tại (alias: `!thoitiet`) |
| `!forecast <thành phố>` | Dự báo thời tiết 5 ngày (alias: `!dubao`) |
| `!hourly <thành phố>` | Dự báo theo giờ (alias: `!theogio`) |
| `!alerts <thành phố>` | Cảnh báo thời tiết (alias: `!canhbao`) |

---

## Đếm ngược / Kỳ thi

| Lệnh | Mô tả |
|---|---|
| `/remain` | Xem còn bao nhiêu ngày đến kỳ thi |
| `!remainthpt` | Đếm ngược ngày thi THPT Quốc Gia (aliases: `!countdown`, `!thpt`) |
| `/remainthpt` | Đếm ngược THPT (slash command) |

---

## Tư vấn

| Lệnh | Mô tả |
|---|---|
| `!tuvan` | Hiển thị menu chọn kênh tư vấn (alias: `!tv`) |
| `/tuvan` | Hiển thị menu tư vấn (slash command) |

---

## Sự kiện

| Lệnh | Mô tả |
|---|---|
| `/sukien` | Xem danh sách sự kiện trong server (có thể lọc theo loại) |
| `/sukien_chitiet <tên>` | Xem chi tiết một sự kiện cụ thể |

---

## Hỗ trợ & Gọi Mod

| Lệnh | Mô tả |
|---|---|
| `!call 911` | Gọi hỗ trợ khẩn cấp — xem mod đang hoạt động |
| `/call` | Gọi hỗ trợ khẩn cấp (slash command) |
| `!recap [số_tin]` | Tóm tắt đoạn chat gần đây (alias: `!tomtatchat`, cần role) |
| `/recap` | Tóm tắt chat gần đây (slash command, cần role) |

---

## Quản lý (Mod/Admin)

| Lệnh | Quyền cần | Mô tả |
|---|---|---|
| `!stop @user [phút]` | Role cụ thể | Timeout thành viên (mặc định 10 phút, tối đa 28 ngày) |
| `!delete @user [số_lượng]` | Manage Messages + Role | Xóa tin nhắn của người dùng trên toàn server |
| `!2timeout` | Role cụ thể | Xem lý do timeout |
| `!setremainch #channel` | Admin | Thêm kênh nhận thông báo THPT |
| `!removeremainch #channel` | Admin | Xóa kênh nhận thông báo THPT |
| `!listremainch` | Admin | Liệt kê các kênh nhận thông báo THPT |
| `!modresetquiz` | Admin | Reset quiz cho toàn server |

---

## Tiện ích khác

| Lệnh | Mô tả |
|---|---|
| `!sysinfo` | Xem cấu hình hệ thống đang chạy bot (aliases: `!system`, `!cauhinh`, `!vga`, `!gpu`) |
| `!test_donate` | Xem trước tin nhắn donate (alias: `!testdonate`) |
| `!scheduled_list` | Xem danh sách lịch gửi tin (chỉ owner) |
| `!test_schedule <index>` | Test gửi tin theo lịch (chỉ owner) |
| `!testlast` | Test kiểm tra tin nhắn cuối (chỉ owner) |
| `!forcechecklast` | Ép kiểm tra tin nhắn cuối (chỉ owner) |

---

## Tính năng tự động

Các tính năng dưới đây hoạt động **tự động**, không cần gõ lệnh:

| Tính năng | Mô tả |
|---|---|
| **Lọc từ cấm** | Tự động xóa tin nhắn chứa từ/cụm cấm và timeout người vi phạm |
| **Kiểm duyệt tục tĩu (AI)** | Tag bot vào tin nhắn hoặc reply — bot dùng AI phân tích và timeout nếu vượt ngưỡng |
| **Chống spam (AI)** | Tự động phát hiện spam/scam trong kênh được cấu hình, xóa tin + timeout. Quét định kỳ mỗi giờ |
| **Trigger tự động** | Bot tự reply khi phát hiện cụm từ trigger trong tin nhắn (ví dụ: "ai hỏi") |
| **Chào thành viên mới** | Tự động gửi hướng dẫn khi có người mới join server |
| **Tin nhắn buổi sáng** | Gửi tin nhắn tự động lúc 5:30 AM (GMT+7) |
| **Đếm ngược sự kiện** | Gửi tự động lúc 8:00 AM & 11:00 PM (GMT+7) |
| **Nhắc thi THPT** | Gửi tự động lúc 7:30 AM & 11:30 PM (GMT+7) |
