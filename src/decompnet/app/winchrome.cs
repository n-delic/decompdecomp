// A client-drawn caption with real Windows hit tests. WebView2 sits below it, so it cannot swallow resize,
// caption or system-button input. DefWindowProc owns moving, sizing, snapping and caption-button tracking.
using System;
using System.Drawing;
using System.Runtime.InteropServices;
using System.Windows.Forms;

namespace Decompdecomp {
    public sealed class WindowChrome : NativeWindow {
        const int WM_NCCALCSIZE = 0x83, WM_NCHITTEST = 0x84, WM_GETMINMAXINFO = 0x24;
        const int WM_NCMOUSEMOVE = 0xA0, WM_NCMOUSELEAVE = 0x2A2, WM_DPICHANGED = 0x2E0;
        const int HTCLIENT = 1, HTCAPTION = 2, HTMINBUTTON = 8, HTMAXBUTTON = 9, HTCLOSE = 20;
        const int HTTRANSPARENT = -1, HTLEFT = 10, HTRIGHT = 11, HTTOP = 12, HTTOPLEFT = 13, HTTOPRIGHT = 14;
        const int HTBOTTOM = 15, HTBOTTOMLEFT = 16, HTBOTTOMRIGHT = 17;
        const uint SWP_NOSIZE = 1, SWP_NOMOVE = 2, SWP_NOZORDER = 4, SWP_FRAMECHANGED = 0x20;
        const uint TME_LEAVE = 2, TME_NONCLIENT = 0x10;
        readonly Form form;
        readonly Caption caption;
        readonly CaptionButton[] buttons;
        int border, captionHeight, buttonWidth;
        Color hover = Color.FromArgb(48, 47, 42), danger = Color.FromArgb(196, 43, 28);

        [DllImport("user32.dll")] static extern uint GetDpiForWindow(IntPtr hwnd);
        [DllImport("user32.dll")] static extern bool TrackMouseEvent(ref MouseTracking tracking);
        [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int cx, int cy, uint flags);
        [DllImport("dwmapi.dll")] static extern int DwmExtendFrameIntoClientArea(IntPtr hwnd, ref Margins margins);
        [StructLayout(LayoutKind.Sequential)] struct MouseTracking {
            public int size; public uint flags; public IntPtr hwnd; public uint time;
        }
        [StructLayout(LayoutKind.Sequential)] struct Margins { public int left, right, top, bottom; }
        [StructLayout(LayoutKind.Sequential)] struct Point { public int x, y; }
        [StructLayout(LayoutKind.Sequential)] struct MinMaxInfo {
            public Point reserved, maxSize, maxPosition, minTrackSize, maxTrackSize;
        }

        public WindowChrome(Form form) {
            this.form = form;
            AssignHandle(form.Handle);
            caption = new Caption(form);
            buttons = new CaptionButton[] {
                new CaptionButton(HTMINBUTTON), new CaptionButton(HTMAXBUTTON), new CaptionButton(HTCLOSE)
            };
            foreach (CaptionButton button in buttons) {
                caption.Controls.Add(button);
                button.Click += delegate { Activate(button.Hit); };
            }
            form.Controls.Add(caption);
            caption.BringToFront();
            form.SizeChanged += delegate { Layout(); };
            form.Activated += delegate { caption.Invalidate(); };
            form.Deactivate += delegate { caption.Invalidate(); };
            form.FormClosed += delegate { ReleaseHandle(); };
            SetTheme("#22211e", "#eeece6", "#34332e", "#c42b1c");
            Layout();
            // Recalculate after attaching our NCCALCSIZE handler, without ever changing the sizeable window style.
            SetWindowPos(Handle, IntPtr.Zero, 0, 0, 0, 0, SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER);
            Margins margins = new Margins { left = 1, right = 1, top = 1, bottom = 1 };
            DwmExtendFrameIntoClientArea(Handle, ref margins);
        }

        void Activate(int hit) {
            if (hit == HTCLOSE) form.Close();
            else if (hit == HTMINBUTTON) form.WindowState = FormWindowState.Minimized;
            else form.WindowState = form.WindowState == FormWindowState.Maximized
                ? FormWindowState.Normal : FormWindowState.Maximized;
        }

        void Layout() {
            float scale = GetDpiForWindow(Handle) / 96f;
            bool maximized = form.WindowState == FormWindowState.Maximized;
            border = maximized ? 0 : (int)Math.Ceiling(6 * scale);
            captionHeight = (int)Math.Round(34 * scale);
            buttonWidth = (int)Math.Round(46 * scale);
            form.Padding = new Padding(border, border + captionHeight, border, border);
            caption.SetBounds(border, border, Math.Max(0, form.ClientSize.Width - border * 2), captionHeight);
            for (int index = 0; index < buttons.Length; index++) {
                CaptionButton button = buttons[index];
                button.SetBounds(caption.Width - (3 - index) * buttonWidth, 0, buttonWidth, captionHeight);
                button.Restore = maximized;
                button.AccessibleName = index == 0 ? "Minimize" : index == 2 ? "Close" : maximized ? "Restore" : "Maximize";
                button.Invalidate();
            }
        }

        public void SetTheme(string background, string foreground, string hoverColor, string dangerColor) {
            form.BackColor = caption.BackColor = ColorTranslator.FromHtml(background);
            caption.ForeColor = ColorTranslator.FromHtml(foreground);
            hover = ColorTranslator.FromHtml(hoverColor);
            danger = ColorTranslator.FromHtml(dangerColor);
            foreach (CaptionButton button in buttons) {
                button.BackColor = caption.BackColor;
                button.ForeColor = caption.ForeColor;
                button.HoverColor = button.Hit == HTCLOSE ? danger : hover;
                button.Invalidate();
            }
            caption.Invalidate();
            form.Invalidate();
        }

        // Coordinates and dimensions are physical pixels on the window's current monitor.
        public static int HitTest(int x, int y, int width, int height, int edge, int title, int button, bool maximized) {
            if (!maximized) {
                bool left = x < edge, right = x >= width - edge;
                if (y < edge) return left ? HTTOPLEFT : right ? HTTOPRIGHT : HTTOP;
                if (y >= height - edge) return left ? HTBOTTOMLEFT : right ? HTBOTTOMRIGHT : HTBOTTOM;
                if (left) return HTLEFT;
                if (right) return HTRIGHT;
            }
            if (y < edge + title) {
                if (x >= width - edge - button) return HTCLOSE;
                if (x >= width - edge - button * 2) return HTMAXBUTTON;
                if (x >= width - edge - button * 3) return HTMINBUTTON;
                return HTCAPTION;
            }
            return HTCLIENT;
        }

        protected override void WndProc(ref Message message) {
            if (message.Msg == WM_NCCALCSIZE) { message.Result = IntPtr.Zero; return; }
            if (message.Msg == WM_NCHITTEST) {
                long coords = message.LParam.ToInt64();
                System.Drawing.Point point = form.PointToClient(new System.Drawing.Point((short)coords, (short)(coords >> 16)));
                int hit = HitTest(point.X, point.Y, form.ClientSize.Width, form.ClientSize.Height,
                                  border, captionHeight, buttonWidth, form.WindowState == FormWindowState.Maximized);
                message.Result = new IntPtr(hit);
                return;
            }
            if (message.Msg == WM_GETMINMAXINFO) {
                base.WndProc(ref message);
                // Borderless maximization otherwise covers the taskbar, and fails on monitors left of the primary.
                Screen screen = Screen.FromHandle(Handle);
                MinMaxInfo info = (MinMaxInfo)Marshal.PtrToStructure(message.LParam, typeof(MinMaxInfo));
                info.maxPosition.x = screen.WorkingArea.Left - screen.Bounds.Left;
                info.maxPosition.y = screen.WorkingArea.Top - screen.Bounds.Top;
                info.maxSize.x = screen.WorkingArea.Width;
                info.maxSize.y = screen.WorkingArea.Height;
                Marshal.StructureToPtr(info, message.LParam, false);
                return;
            }
            if (message.Msg == WM_NCMOUSEMOVE || message.Msg == WM_NCMOUSELEAVE) {
                int hit = message.Msg == WM_NCMOUSEMOVE ? message.WParam.ToInt32() : 0;
                foreach (CaptionButton button in buttons) {
                    button.Hovered = button.Hit == hit;
                    button.Invalidate();
                }
                if (hit != 0) {
                    MouseTracking tracking = new MouseTracking { size = Marshal.SizeOf(typeof(MouseTracking)),
                        flags = TME_LEAVE | TME_NONCLIENT, hwnd = Handle };
                    TrackMouseEvent(ref tracking);
                }
            }
            // Windows handles non-client button input itself, including the Windows 11 maximize hover menu.
            base.WndProc(ref message);
            if (message.Msg == WM_DPICHANGED) Layout();
        }

        sealed class Caption : Panel {
            readonly Form form;
            public Caption(Form form) { this.form = form; DoubleBuffered = true; }
            protected override void WndProc(ref Message message) {
                if (message.Msg == WM_NCHITTEST) { message.Result = new IntPtr(HTTRANSPARENT); return; }
                base.WndProc(ref message);
            }
            protected override void OnPaint(PaintEventArgs args) {
                base.OnPaint(args);
                float scale = Height / 34f;
                int iconSize = (int)(18 * scale);
                if (form.Icon != null) args.Graphics.DrawIcon(form.Icon, new Rectangle((int)(8 * scale), (Height - iconSize) / 2, iconSize, iconSize));
                using (Font font = new Font("Segoe UI", 9)) {
                    TextRenderer.DrawText(args.Graphics, form.Text, font, new Rectangle((int)(34 * scale), 0,
                        Math.Max(0, Width - (int)(180 * scale)), Height), ForeColor,
                        TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis | TextFormatFlags.SingleLine);
                }
            }
        }

        sealed class CaptionButton : Button {
            public readonly int Hit;
            public bool Restore, Hovered;
            public Color HoverColor;
            public CaptionButton(int hit) {
                Hit = hit; TabStop = false; AccessibleRole = AccessibleRole.PushButton;
                SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer, true);
            }
            protected override void WndProc(ref Message message) {
                if (message.Msg == WM_NCHITTEST) { message.Result = new IntPtr(HTTRANSPARENT); return; }
                base.WndProc(ref message);
            }
            protected override void OnPaint(PaintEventArgs args) {
                args.Graphics.Clear(Hovered ? HoverColor : BackColor);
                float scale = Height / 34f, size = 10 * scale;
                float left = (Width - size) / 2, top = (Height - size) / 2;
                using (Pen pen = new Pen(Hovered && Hit == HTCLOSE ? Color.White : ForeColor, Math.Max(1, scale))) {
                    if (Hit == HTMINBUTTON) args.Graphics.DrawLine(pen, left, top + size / 2, left + size, top + size / 2);
                    else if (Hit == HTCLOSE) {
                        args.Graphics.DrawLine(pen, left, top, left + size, top + size);
                        args.Graphics.DrawLine(pen, left + size, top, left, top + size);
                    } else if (Restore) {
                        args.Graphics.DrawLines(pen, new PointF[] { new PointF(left + 2 * scale, top + 2 * scale),
                            new PointF(left + 2 * scale, top), new PointF(left + size, top), new PointF(left + size, top + size - 2 * scale),
                            new PointF(left + size - 2 * scale, top + size - 2 * scale) });
                        args.Graphics.DrawRectangle(pen, left, top + 2 * scale, size - 2 * scale, size - 2 * scale);
                    } else args.Graphics.DrawRectangle(pen, left, top, size, size);
                }
            }
        }
    }
}
