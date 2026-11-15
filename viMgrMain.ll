; ModuleID = '/tmp/claude-501/tb/src/viMgrMain.c'
source_filename = "/tmp/claude-501/tb/src/viMgrMain.c"
target datalayout = "e-m:e-p:32:32-p10:8:8-p20:8:8-i64:64-n32:64-S128-ni:1:10:20"
target triple = "wasm32-unknown-unknown-unknown"

%struct.OSDevMgr = type { i32, ptr, ptr, ptr, ptr, ptr, ptr }
%struct.OSIoMesg = type { %struct.anon, [16 x i8] }
%struct.anon = type { i16, i8, i8, ptr }
%struct.__OSViContext = type { i16, i16, ptr, ptr, i32, ptr, ptr, %struct.__OSViScale, %struct.__OSViScale }
%struct.__OSViScale = type { float, i16, i32 }

@D_800EF240 = hidden global %struct.OSDevMgr zeroinitializer, align 4
@D_80138E80 = external global [0 x i8], align 1
@D_80138E98 = external global [0 x i8], align 1
@D_80138EB0 = hidden global %struct.OSIoMesg zeroinitializer, align 4
@D_80138EC8 = hidden global %struct.OSIoMesg zeroinitializer, align 4
@D_80137CD0 = external global [0 x i8], align 1
@D_80137E80 = external global [0 x i8], align 1
@D_80138EE0 = external global i16, align 2
@D_80138F5C = external global i32, align 4
@D_80138F50 = external global i64, align 8
@D_80138F58 = external global i32, align 4

; Function Attrs: noinline nounwind optnone uwtable
define hidden void @osCreateViManager(i32 noundef %0) #0 {
  %2 = alloca i32, align 4
  %3 = alloca i32, align 4
  %4 = alloca i32, align 4
  %5 = alloca i32, align 4
  store i32 %0, ptr %2, align 4
  %6 = load i32, ptr @D_800EF240, align 4
  %7 = icmp ne i32 %6, 0
  br i1 %7, label %8, label %9

8:                                                ; preds = %1
  br label %25

9:                                                ; preds = %1
  call void @__osTimerServicesInit() #2
  call void @osCreateMesgQueue(ptr noundef @D_80138E80, ptr noundef @D_80138E98, i32 noundef 5) #2
  store i16 13, ptr @D_80138EB0, align 4
  store i8 0, ptr getelementptr inbounds (%struct.anon, ptr @D_80138EB0, i32 0, i32 1), align 2
  store ptr null, ptr getelementptr inbounds (%struct.anon, ptr @D_80138EB0, i32 0, i32 3), align 4
  store i16 14, ptr @D_80138EC8, align 4
  store i8 0, ptr getelementptr inbounds (%struct.anon, ptr @D_80138EC8, i32 0, i32 1), align 2
  store ptr null, ptr getelementptr inbounds (%struct.anon, ptr @D_80138EC8, i32 0, i32 3), align 4
  call void @osSetEventMesg(i32 noundef 7, ptr noundef @D_80138E80, ptr noundef @D_80138EB0) #2
  call void @osSetEventMesg(i32 noundef 3, ptr noundef @D_80138E80, ptr noundef @D_80138EC8) #2
  store i32 -1, ptr %4, align 4
  %10 = call i32 @osGetThreadPri(ptr noundef null) #2
  store i32 %10, ptr %5, align 4
  %11 = load i32, ptr %5, align 4
  %12 = load i32, ptr %2, align 4
  %13 = icmp slt i32 %11, %12
  br i1 %13, label %14, label %17

14:                                               ; preds = %9
  %15 = load i32, ptr %5, align 4
  store i32 %15, ptr %4, align 4
  %16 = load i32, ptr %2, align 4
  call void @osSetThreadPri(ptr noundef null, i32 noundef %16) #2
  br label %17

17:                                               ; preds = %14, %9
  %18 = call i32 @__osDisableInt() #2
  store i32 %18, ptr %3, align 4
  store i32 1, ptr @D_800EF240, align 4
  store ptr @D_80137CD0, ptr getelementptr inbounds (%struct.OSDevMgr, ptr @D_800EF240, i32 0, i32 1), align 4
  store ptr @D_80138E80, ptr getelementptr inbounds (%struct.OSDevMgr, ptr @D_800EF240, i32 0, i32 2), align 4
  store ptr @D_80138E80, ptr getelementptr inbounds (%struct.OSDevMgr, ptr @D_800EF240, i32 0, i32 3), align 4
  store ptr null, ptr getelementptr inbounds (%struct.OSDevMgr, ptr @D_800EF240, i32 0, i32 4), align 4
  store ptr null, ptr getelementptr inbounds (%struct.OSDevMgr, ptr @D_800EF240, i32 0, i32 5), align 4
  store ptr null, ptr getelementptr inbounds (%struct.OSDevMgr, ptr @D_800EF240, i32 0, i32 6), align 4
  %19 = load i32, ptr %2, align 4
  call void @osCreateThread(ptr noundef @D_80137CD0, i32 noundef 0, ptr noundef @viMgrMain, ptr noundef @D_800EF240, ptr noundef getelementptr (i8, ptr @D_80137E80, i32 4096), i32 noundef %19) #2
  call void @__osViInit() #2
  call void @osStartThread(ptr noundef @D_80137CD0) #2
  %20 = load i32, ptr %3, align 4
  call void @__osRestoreInt(i32 noundef %20) #2
  %21 = load i32, ptr %4, align 4
  %22 = icmp ne i32 %21, -1
  br i1 %22, label %23, label %25

23:                                               ; preds = %17
  %24 = load i32, ptr %4, align 4
  call void @osSetThreadPri(ptr noundef null, i32 noundef %24) #2
  br label %25

25:                                               ; preds = %8, %23, %17
  ret void
}

declare void @__osTimerServicesInit() #1

declare void @osCreateMesgQueue(ptr noundef, ptr noundef, i32 noundef) #1

declare void @osSetEventMesg(i32 noundef, ptr noundef, ptr noundef) #1

declare i32 @osGetThreadPri(ptr noundef) #1

declare void @osSetThreadPri(ptr noundef, i32 noundef) #1

declare i32 @__osDisableInt() #1

declare void @osCreateThread(ptr noundef, i32 noundef, ptr noundef, ptr noundef, ptr noundef, i32 noundef) #1

; Function Attrs: noinline nounwind optnone uwtable
define hidden void @viMgrMain(ptr noundef %0) #0 {
  %2 = alloca ptr, align 4
  %3 = alloca ptr, align 4
  %4 = alloca ptr, align 4
  %5 = alloca ptr, align 4
  %6 = alloca i32, align 4
  %7 = alloca i32, align 4
  store ptr %0, ptr %2, align 4
  store ptr null, ptr %5, align 4
  store i32 0, ptr %6, align 4
  %8 = call ptr @CAttractDemo__PlayingDemo() #2
  store ptr %8, ptr %3, align 4
  %9 = load ptr, ptr %3, align 4
  %10 = getelementptr inbounds %struct.__OSViContext, ptr %9, i32 0, i32 1
  %11 = load i16, ptr %10, align 2
  store i16 %11, ptr @D_80138EE0, align 2
  %12 = load i16, ptr @D_80138EE0, align 2
  %13 = zext i16 %12 to i32
  %14 = icmp eq i32 %13, 0
  br i1 %14, label %15, label %16

15:                                               ; preds = %1
  store i16 1, ptr @D_80138EE0, align 2
  br label %16

16:                                               ; preds = %15, %1
  %17 = load ptr, ptr %2, align 4
  store ptr %17, ptr %4, align 4
  br label %18

18:                                               ; preds = %16, %73
  %19 = load ptr, ptr %4, align 4
  %20 = getelementptr inbounds %struct.OSDevMgr, ptr %19, i32 0, i32 3
  %21 = load ptr, ptr %20, align 4
  %22 = call i32 @osRecvMesg(ptr noundef %21, ptr noundef %5, i32 noundef 1) #2
  %23 = load ptr, ptr %5, align 4
  %24 = getelementptr inbounds %struct.OSIoMesg, ptr %23, i32 0, i32 0
  %25 = getelementptr inbounds %struct.anon, ptr %24, i32 0, i32 0
  %26 = load i16, ptr %25, align 4
  %27 = zext i16 %26 to i32
  switch i32 %27, label %72 [
    i32 13, label %28
    i32 14, label %71
  ]

28:                                               ; preds = %18
  call void @__osViSwapContext() #2
  %29 = load i16, ptr @D_80138EE0, align 2
  %30 = add i16 %29, -1
  store i16 %30, ptr @D_80138EE0, align 2
  %31 = load i16, ptr @D_80138EE0, align 2
  %32 = zext i16 %31 to i32
  %33 = icmp eq i32 %32, 0
  br i1 %33, label %34, label %52

34:                                               ; preds = %28
  %35 = call ptr @CAttractDemo__PlayingDemo() #2
  store ptr %35, ptr %3, align 4
  %36 = load ptr, ptr %3, align 4
  %37 = getelementptr inbounds %struct.__OSViContext, ptr %36, i32 0, i32 5
  %38 = load ptr, ptr %37, align 4
  %39 = icmp ne ptr %38, null
  br i1 %39, label %40, label %48

40:                                               ; preds = %34
  %41 = load ptr, ptr %3, align 4
  %42 = getelementptr inbounds %struct.__OSViContext, ptr %41, i32 0, i32 5
  %43 = load ptr, ptr %42, align 4
  %44 = load ptr, ptr %3, align 4
  %45 = getelementptr inbounds %struct.__OSViContext, ptr %44, i32 0, i32 6
  %46 = load ptr, ptr %45, align 4
  %47 = call i32 @osSendMesg(ptr noundef %43, ptr noundef %46, i32 noundef 0) #2
  br label %48

48:                                               ; preds = %40, %34
  %49 = load ptr, ptr %3, align 4
  %50 = getelementptr inbounds %struct.__OSViContext, ptr %49, i32 0, i32 1
  %51 = load i16, ptr %50, align 2
  store i16 %51, ptr @D_80138EE0, align 2
  br label %52

52:                                               ; preds = %48, %28
  %53 = load i32, ptr @D_80138F5C, align 4
  %54 = add i32 %53, 1
  store i32 %54, ptr @D_80138F5C, align 4
  %55 = load i32, ptr %6, align 4
  %56 = icmp ne i32 %55, 0
  br i1 %56, label %57, label %61

57:                                               ; preds = %52
  %58 = call i32 @osGetCount() #2
  store i32 %58, ptr %7, align 4
  %59 = load i32, ptr %7, align 4
  %60 = zext i32 %59 to i64
  store i64 %60, ptr @D_80138F50, align 8
  store i32 0, ptr %6, align 4
  br label %61

61:                                               ; preds = %57, %52
  %62 = load i32, ptr @D_80138F58, align 4
  store i32 %62, ptr %7, align 4
  %63 = call i32 @osGetCount() #2
  store i32 %63, ptr @D_80138F58, align 4
  %64 = load i32, ptr @D_80138F58, align 4
  %65 = load i32, ptr %7, align 4
  %66 = sub i32 %64, %65
  store i32 %66, ptr %7, align 4
  %67 = load i64, ptr @D_80138F50, align 8
  %68 = load i32, ptr %7, align 4
  %69 = zext i32 %68 to i64
  %70 = add i64 %67, %69
  store i64 %70, ptr @D_80138F50, align 8
  br label %73

71:                                               ; preds = %18
  call void @__osTimerInterrupt() #2
  br label %73

72:                                               ; preds = %18
  br label %73

73:                                               ; preds = %72, %71, %61
  br label %18
}

declare void @__osViInit() #1

declare void @osStartThread(ptr noundef) #1

declare void @__osRestoreInt(i32 noundef) #1

declare ptr @CAttractDemo__PlayingDemo() #1

declare i32 @osRecvMesg(ptr noundef, ptr noundef, i32 noundef) #1

declare void @__osViSwapContext() #1

declare i32 @osSendMesg(ptr noundef, ptr noundef, i32 noundef) #1

declare i32 @osGetCount() #1

declare void @__osTimerInterrupt() #1

attributes #0 = { noinline nounwind optnone uwtable "frame-pointer"="all" "no-builtins" "no-trapping-math"="true" "stack-protector-buffer-size"="8" "target-cpu"="generic" "target-features"="+bulk-memory,+extended-const,+multivalue,+mutable-globals,+nontrapping-fptoint,+sign-ext,-atomics,-exception-handling,-half-precision,-multimemory,-reference-types,-relaxed-simd,-simd128,-tail-call" }
attributes #1 = { "frame-pointer"="all" "no-builtins" "no-trapping-math"="true" "stack-protector-buffer-size"="8" "target-cpu"="generic" "target-features"="+bulk-memory,+extended-const,+multivalue,+mutable-globals,+nontrapping-fptoint,+sign-ext,-atomics,-exception-handling,-half-precision,-multimemory,-reference-types,-relaxed-simd,-simd128,-tail-call" }
attributes #2 = { nobuiltin "no-builtins" }

!llvm.module.flags = !{!0, !1, !2, !3}
!llvm.ident = !{!4}

!0 = !{i32 7, !"Dwarf Version", i32 4}
!1 = !{i32 1, !"wchar_size", i32 4}
!2 = !{i32 7, !"uwtable", i32 2}
!3 = !{i32 7, !"frame-pointer", i32 2}
!4 = !{!"clang version 19.1.7 (https://github.com/ziglang/zig-bootstrap de1b01a8c1dddf75a560123ac1c2ab182b4830da)"}
