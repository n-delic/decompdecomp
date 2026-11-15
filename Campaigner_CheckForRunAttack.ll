; ModuleID = '/tmp/claude-501/tb/src/Campaigner_CheckForRunAttack.c'
source_filename = "/tmp/claude-501/tb/src/Campaigner_CheckForRunAttack.c"
target datalayout = "e-m:e-p:32:32-p10:8:8-p20:8:8-i64:64-n32:64-S128-ni:1:10:20"
target triple = "wasm32-unknown-unknown-unknown"

@D_800B6B00 = external global float, align 4
@D_800F2DB0 = external global double, align 8

; Function Attrs: noinline nounwind optnone uwtable
define hidden i32 @Campaigner_CheckForRunAttack(i32 noundef %0, ptr noundef %1) #0 {
  %3 = alloca i32, align 4
  %4 = alloca ptr, align 4
  store i32 %0, ptr %3, align 4
  store ptr %1, ptr %4, align 4
  %5 = load ptr, ptr %4, align 4
  %6 = getelementptr i8, ptr %5, i32 528
  %7 = load float, ptr %6, align 4
  %8 = load float, ptr @D_800B6B00, align 4
  %9 = fadd float %7, %8
  %10 = load ptr, ptr %4, align 4
  %11 = getelementptr i8, ptr %10, i32 528
  store float %9, ptr %11, align 4
  %12 = load ptr, ptr %4, align 4
  %13 = getelementptr i8, ptr %12, i32 388
  %14 = load ptr, ptr %13, align 4
  %15 = getelementptr i8, ptr %14, i32 8
  %16 = load float, ptr %15, align 4
  %17 = load ptr, ptr %4, align 4
  %18 = getelementptr i8, ptr %17, i32 528
  %19 = load float, ptr %18, align 4
  %20 = fcmp olt float %16, %19
  br i1 %20, label %21, label %33

21:                                               ; preds = %2
  %22 = load ptr, ptr %4, align 4
  %23 = getelementptr i8, ptr %22, i32 332
  %24 = load float, ptr %23, align 4
  %25 = fpext float %24 to double
  %26 = load double, ptr @D_800F2DB0, align 8
  %27 = fcmp ole double %25, %26
  br i1 %27, label %28, label %33

28:                                               ; preds = %21
  %29 = load ptr, ptr %4, align 4
  %30 = getelementptr i8, ptr %29, i32 528
  store float 0.000000e+00, ptr %30, align 4
  %31 = load i32, ptr %3, align 4
  %32 = call i32 @Campaigner_PerformRunAttack(i32 noundef %31) #2
  br label %33

33:                                               ; preds = %28, %21, %2
  %34 = load ptr, ptr %4, align 4
  %35 = getelementptr i8, ptr %34, i32 32
  %36 = load i32, ptr %35, align 4
  %37 = load ptr, ptr %4, align 4
  %38 = getelementptr i8, ptr %37, i32 36
  %39 = load i32, ptr %38, align 4
  %40 = icmp ne i32 %36, %39
  %41 = zext i1 %40 to i32
  ret i32 %41
}

declare i32 @Campaigner_PerformRunAttack(...) #1

attributes #0 = { noinline nounwind optnone uwtable "frame-pointer"="all" "no-builtins" "no-trapping-math"="true" "stack-protector-buffer-size"="8" "target-cpu"="generic" "target-features"="+bulk-memory,+extended-const,+multivalue,+mutable-globals,+nontrapping-fptoint,+sign-ext,-atomics,-exception-handling,-half-precision,-multimemory,-reference-types,-relaxed-simd,-simd128,-tail-call" }
attributes #1 = { "frame-pointer"="all" "no-builtins" "no-prototype" "no-trapping-math"="true" "stack-protector-buffer-size"="8" "target-cpu"="generic" "target-features"="+bulk-memory,+extended-const,+multivalue,+mutable-globals,+nontrapping-fptoint,+sign-ext,-atomics,-exception-handling,-half-precision,-multimemory,-reference-types,-relaxed-simd,-simd128,-tail-call" }
attributes #2 = { nobuiltin "no-builtins" }

!llvm.module.flags = !{!0, !1, !2, !3}
!llvm.ident = !{!4}

!0 = !{i32 7, !"Dwarf Version", i32 4}
!1 = !{i32 1, !"wchar_size", i32 4}
!2 = !{i32 7, !"uwtable", i32 2}
!3 = !{i32 7, !"frame-pointer", i32 2}
!4 = !{!"clang version 19.1.7 (https://github.com/ziglang/zig-bootstrap de1b01a8c1dddf75a560123ac1c2ab182b4830da)"}
