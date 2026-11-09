using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows.Forms;
internal static class Probe {
 [DllImport("user32.dll")] static extern bool SetProcessDpiAwarenessContext(IntPtr v);
 [DllImport("user32.dll")] static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] static extern void mouse_event(uint f,uint x,uint y,uint d,UIntPtr info);
 [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb,IntPtr p);
 [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h,out uint pid);
 [DllImport("user32.dll")] static extern bool GetClientRect(IntPtr h,out RECT r);
 [DllImport("user32.dll")] static extern bool ClientToScreen(IntPtr h,ref POINT p);
 [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
 delegate bool EnumProc(IntPtr h,IntPtr p);
 struct RECT {public int l,t,r,b;} struct POINT {public int x,y;}
 [STAThread] static void Main(string[] args) {
  if(args.Length<2)return;SetProcessDpiAwarenessContext((IntPtr)(-4));
  uint wanted=uint.Parse(args[0]);IntPtr target=IntPtr.Zero;
  EnumWindows(delegate(IntPtr h,IntPtr p){uint pid;GetWindowThreadProcessId(h,out pid);if(pid==wanted && IsWindowVisible(h))target=h;return true;},IntPtr.Zero);
  if(target==IntPtr.Zero)throw new Exception("Own desktop host not found");
  RECT area;GetClientRect(target,out area);POINT drop=new POINT{x=(area.r-area.l)/2,y=(area.b-area.t)/2};ClientToScreen(target,ref drop);
  var source=new Form{Text="SCNU OLE test source",Location=new Point(30,30),Size=new Size(220,100),StartPosition=FormStartPosition.Manual};
  var label=new Label{Text="Native OLE file drag test",Dock=DockStyle.Fill};source.Controls.Add(label);
  source.Shown+=delegate {source.BeginInvoke((Action)delegate{
   SetCursorPos(source.Left+60,source.Top+50);mouse_event(2,0,0,0,UIntPtr.Zero);
   var timer=new Timer{Interval=400};int tick=0;
   timer.Tick+=delegate{tick++;if(tick==1){SetCursorPos(drop.x,drop.y);}else if(tick==3){mouse_event(4,0,0,0,UIntPtr.Zero);timer.Stop();}};timer.Start();
   var data=new DataObject();data.SetData(DataFormats.FileDrop,new[]{Path.GetFullPath(args[1])});
   var effect=source.DoDragDrop(data,DragDropEffects.Copy);timer.Dispose();Console.WriteLine("OLE_EFFECT="+effect);source.Close();
  });};Application.Run(source);
 }
}
