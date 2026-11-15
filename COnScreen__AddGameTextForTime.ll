; ModuleID = '/tmp/claude-501/tb/src/COnScreen__AddGameTextForTime.c'
source_filename = "/tmp/claude-501/tb/src/COnScreen__AddGameTextForTime.c"
target datalayout = "e-m:e-p:32:32-p10:8:8-p20:8:8-i64:64-n32:64-S128-ni:1:10:20"
target triple = "wasm32-unknown-unknown-unknown"

; Function Attrs: noinline nounwind optnone uwtable
define hidden ptr @COnScreen__AddGameTextForTime(i32 noundef %0, float noundef %1) #0 {
  %3 = alloca i32, align 4
  %4 = alloca float, align 4
  %5 = alloca ptr, align 4
  store i32 %0, ptr %3, align 4
  store float %1, ptr %4, align 4
  %6 = load i32, ptr %3, align 4
  %7 = call ptr @COnScreen__AddGameText(i32 noundef %6) #2
  store ptr %7, ptr %5, align 4
  %8 = load ptr, ptr %5, align 4
  %9 = icmp ne ptr %8, null
  br i1 %9, label %10, label %15

10:                                               ; preds = %2
  %11 = load float, ptr %4, align 4
  %12 = fmul float %11, 1.500000e+01
  %13 = load ptr, ptr %5, align 4
  %14 = getelementptr i8, ptr %13, i32 4
  store float %12, ptr %14, align 4
  br label %15

15:                                               ; preds = %10, %2
  %16 = load ptr, ptr %5, align 4
  ret ptr %16
}

declare ptr @COnScreen__AddGameText(...) #1

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
