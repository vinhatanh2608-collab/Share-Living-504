import Modal from "./Modal";

export type GuideDestination = "chat" | "advisor" | "groups" | "debts";

export default function UserGuideModal({ close, go }: {
  close: () => void;
  go?: (destination: GuideDestination) => void;
}) {
  return <Modal title="Trung tâm Hướng dẫn sử dụng" close={close}>
    <div className="form user-guide">
      <p>Bắt đầu ở mục <strong>Nhóm</strong> để tạo nhóm hoặc nhập mã mời. Khi được duyệt, chọn nhóm ở đầu màn hình.</p>
      <details open className="living-card">
        <summary>Trợ lý AI và nhập nhanh trong trò chuyện</summary>
        <ol>
          <li>Mở <strong>Trò chuyện nhóm</strong> và nhập lệnh vào ô tin nhắn.</li>
          <li><code>/chi 240k tiền lẩu tối qua, An trả, Bình không ăn</code></li>
          <li><strong>trả</strong> hoặc <strong>góp</strong> đánh dấu người đã góp. <strong>không ăn</strong>, <strong>vắng</strong> hoặc <strong>không tham gia</strong> loại người đó khỏi lần chia. Những người còn lại chưa góp.</li>
          <li>Gửi lệnh, kiểm tra số tiền và tên trên thẻ xem trước, rồi chọn <strong>Xác nhận tạo khoản chi</strong>. Nếu sai, người tạo có thể hủy bản nháp và nhập lại.</li>
          <li>Mở <strong>Trợ lý</strong> để xem xu hướng chi tiêu và cảnh báo từ 80% hạn mức. Chủ nhóm đặt hạn mức ngay trên thẻ ngân sách.</li>
        </ol>
        {go && <div className="living-buttons"><button className="secondary" onClick={() => go("chat")}>Mở trò chuyện nhóm</button>
          <button className="secondary" onClick={() => go("advisor")}>Mở Trợ lý</button></div>}
      </details>
      <details className="living-card">
        <summary>Chia tiền, quỹ chung và tiền điện nước</summary>
        <ol>
          <li>Trong <strong>Nhóm</strong>, mở <strong>Thêm chi tiêu nhóm</strong> và chọn cách chia.</li>
          <li>Ví dụ, An ứng 240.000 đồng cho bữa ăn của An và Chi. An chọn <strong>Đã góp tiền</strong>, Chi chọn <strong>Chưa góp tiền</strong>, Bình không ăn nên chọn <strong>Không liên quan</strong>. Nếu chia đều, Chi cần trả An 120.000 đồng.</li>
          <li>Vào <strong>Quỹ chung nhóm → Nạp quỹ</strong> để ghi khoản đã đóng. Khi thanh toán bằng tiền quỹ, chọn <strong>Trích từ quỹ chung</strong> để trừ số dư quỹ mà không tính nợ cá nhân.</li>
          <li>Mở <strong>Tính theo chỉ số điện/nước</strong>, bật tính theo chỉ số, nhập chỉ số cũ, chỉ số mới và đơn giá. Ví dụ, từ 1.240 lên 1.395 với đơn giá 3.500 đồng thì tiền điện là 542.500 đồng. Chọn <strong>Lưu làm mẫu</strong> để dùng lại.</li>
        </ol>
        {go && <button className="secondary" onClick={() => go("groups")}>Mở trang Nhóm</button>}
      </details>
      <details className="living-card">
        <summary>Cấn trừ nợ chéo và thanh toán VietQR</summary>
        <ol>
          <li>Nếu An nợ Bình 100.000 đồng và Bình nợ Chi 100.000 đồng, sau cấn trừ An chỉ cần chuyển Chi 100.000 đồng. Bình không phải nhận rồi chuyển tiếp.</li>
          <li>Mở <strong>Công nợ &amp; VietQR</strong> và chọn <strong>Xem chi tiết cách tính</strong> để đối chiếu.</li>
          <li>Chọn <strong>Thanh toán</strong>, dùng ứng dụng ngân hàng quét VietQR của người nhận và kiểm tra số tiền trước khi chuyển. Người nhận cần thiết lập VietQR trong phần cài đặt nhóm.</li>
          <li>Chọn <strong>Nộp ảnh xác minh</strong>, chụp hoặc chọn ảnh biên lai rồi gửi. Người nhận kiểm tra và xác nhận trên thẻ biên lai.</li>
        </ol>
        {go && <button className="secondary" onClick={() => go("debts")}>Mở Công nợ &amp; VietQR</button>}
      </details>
      <details className="living-card">
        <summary>Nhắc nợ cuối tháng và xuất báo cáo</summary>
        <ol>
          <li>Khi tính năng tự động được bật, thẻ nhắc nợ xuất hiện trong trò chuyện nhóm vào cuối tháng, mặc định lúc <strong>20:00 giờ Việt Nam</strong>.</li>
          <li>Trong <strong>Nhóm → Báo cáo quyết toán tháng</strong>, chọn tháng rồi bấm <strong>Tải Excel</strong> (.xlsx) hoặc <strong>Tải CSV</strong> (.csv).</li>
        </ol>
        {go && <button className="secondary" onClick={() => go("groups")}>Mở trang Nhóm</button>}
      </details>
      <button className="secondary" onClick={close}>Đóng hướng dẫn</button>
    </div>
  </Modal>;
}
