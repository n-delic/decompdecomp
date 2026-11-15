; ModuleID = '/tmp/claude-501/tb/src/alHeapDBAlloc.c'
source_filename = "/tmp/claude-501/tb/src/alHeapDBAlloc.c"
target datalayout = "e-m:e-p:32:32-p10:8:8-p20:8:8-i64:64-n32:64-S128-ni:1:10:20"
target triple = "wasm32-unknown-unknown-unknown"

%struct.ALHeap = type { ptr, ptr, i32 }

; Function Attrs: noinline nounwind optnone uwtable
define hidden ptr @alHeapDBAlloc(ptr noundef %0, i32 noundef %1, ptr noundef %2, i32 noundef %3, i32 noundef %4) #0 {
  %6 = alloca ptr, align 4
  %7 = alloca i32, align 4
  %8 = alloca ptr, align 4
  %9 = alloca i32, align 4
  %10 = alloca i32, align 4
  %11 = alloca i32, align 4
  %12 = alloca ptr, align 4
  store ptr %0, ptr %6, align 4
  store i32 %1, ptr %7, align 4
  store ptr %2, ptr %8, align 4
  store i32 %3, ptr %9, align 4
  store i32 %4, ptr %10, align 4
  store ptr null, ptr %12, align 4
  %13 = load i32, ptr %9, align 4
  %14 = load i32, ptr %10, align 4
  %15 = mul i32 %13, %14
  %16 = add i32 %15, 15
  %17 = and i32 %16, -16
  store i32 %17, ptr %11, align 4
  %18 = load ptr, ptr %8, align 4
  %19 = getelementptr inbounds %struct.ALHeap, ptr %18, i32 0, i32 1
  %20 = load ptr, ptr %19, align 4
  %21 = load i32, ptr %11, align 4
  %22 = getelementptr i8, ptr %20, i32 %21
  %23 = load ptr, ptr %8, align 4
  %24 = getelementptr inbounds %struct.ALHeap, ptr %23, i32 0, i32 0
  %25 = load ptr, ptr %24, align 4
  %26 = load ptr, ptr %8, align 4
  %27 = getelementptr inbounds %struct.ALHeap, ptr %26, i32 0, i32 2
  %28 = load i32, ptr %27, align 4
  %29 = getelementptr i8, ptr %25, i32 %28
  %30 = icmp ule ptr %22, %29
  br i1 %30, label %31, label %40

31:                                               ; preds = %5
  %32 = load ptr, ptr %8, align 4
  %33 = getelementptr inbounds %struct.ALHeap, ptr %32, i32 0, i32 1
  %34 = load ptr, ptr %33, align 4
  store ptr %34, ptr %12, align 4
  %35 = load i32, ptr %11, align 4
  %36 = load ptr, ptr %8, align 4
  %37 = getelementptr inbounds %struct.ALHeap, ptr %36, i32 0, i32 1
  %38 = load ptr, ptr %37, align 4
  %39 = getelementptr i8, ptr %38, i32 %35
  store ptr %39, ptr %37, align 4
  br label %41

40:                                               ; preds = %5
  br label %41

41:                                               ; preds = %40, %31
  %42 = load ptr, ptr %12, align 4
  ret ptr %42
}

attributes #0 = { noinline nounwind optnone uwtable "frame-pointer"="all" "no-builtins" "no-trapping-math"="true" "stack-protector-buffer-size"="8" "target-cpu"="generic" "target-features"="+bulk-memory,+extended-const,+multivalue,+mutable-globals,+nontrapping-fptoint,+sign-ext,-atomics,-exception-handling,-half-precision,-multimemory,-reference-types,-relaxed-simd,-simd128,-tail-call" }

!llvm.module.flags = !{!0, !1, !2, !3}
!llvm.ident = !{!4}

!0 = !{i32 7, !"Dwarf Version", i32 4}
!1 = !{i32 1, !"wchar_size", i32 4}
!2 = !{i32 7, !"uwtable", i32 2}
!3 = !{i32 7, !"frame-pointer", i32 2}
!4 = !{!"clang version 19.1.7 (https://github.com/ziglang/zig-bootstrap de1b01a8c1dddf75a560123ac1c2ab182b4830da)"}
