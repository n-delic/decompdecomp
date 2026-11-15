; ModuleID = '/tmp/claude-501/tb/src/CTMove__GetAmmoOfSelectedWeapon.c'
source_filename = "/tmp/claude-501/tb/src/CTMove__GetAmmoOfSelectedWeapon.c"
target datalayout = "e-m:e-p:32:32-p10:8:8-p20:8:8-i64:64-n32:64-S128-ni:1:10:20"
target triple = "wasm32-unknown-unknown-unknown"

%struct.B = type { [320 x i8], [13 x i32], [330 x i8], i8 }

; Function Attrs: noinline nounwind optnone uwtable
define hidden i32 @CTMove__GetAmmoOfSelectedWeapon(ptr noundef %0) #0 {
  %2 = alloca i32, align 4
  %3 = alloca ptr, align 4
  %4 = alloca i32, align 4
  store ptr %0, ptr %3, align 4
  store i32 -1, ptr %4, align 4
  %5 = load ptr, ptr %3, align 4
  %6 = getelementptr inbounds %struct.B, ptr %5, i32 0, i32 3
  %7 = load i8, ptr %6, align 2
  %8 = zext i8 %7 to i32
  switch i32 %8, label %88 [
    i32 100, label %9
    i32 101, label %14
    i32 102, label %19
    i32 103, label %35
    i32 104, label %40
    i32 105, label %45
    i32 106, label %61
    i32 107, label %67
    i32 108, label %72
    i32 109, label %77
    i32 110, label %83
    i32 111, label %83
    i32 112, label %83
    i32 113, label %83
    i32 114, label %83
  ]

9:                                                ; preds = %1
  %10 = load ptr, ptr %3, align 4
  %11 = getelementptr inbounds %struct.B, ptr %10, i32 0, i32 1
  %12 = getelementptr [13 x i32], ptr %11, i32 0, i32 5
  %13 = load i32, ptr %12, align 4
  store i32 %13, ptr %2, align 4
  br label %90

14:                                               ; preds = %1
  %15 = load ptr, ptr %3, align 4
  %16 = getelementptr inbounds %struct.B, ptr %15, i32 0, i32 1
  %17 = getelementptr [13 x i32], ptr %16, i32 0, i32 6
  %18 = load i32, ptr %17, align 4
  store i32 %18, ptr %2, align 4
  br label %90

19:                                               ; preds = %1
  %20 = load ptr, ptr %3, align 4
  %21 = getelementptr inbounds %struct.B, ptr %20, i32 0, i32 1
  %22 = getelementptr [13 x i32], ptr %21, i32 0, i32 3
  %23 = load i32, ptr %22, align 4
  %24 = icmp ne i32 %23, 0
  br i1 %24, label %25, label %30

25:                                               ; preds = %19
  %26 = load ptr, ptr %3, align 4
  %27 = getelementptr inbounds %struct.B, ptr %26, i32 0, i32 1
  %28 = getelementptr [13 x i32], ptr %27, i32 0, i32 3
  %29 = load i32, ptr %28, align 4
  store i32 %29, ptr %2, align 4
  br label %90

30:                                               ; preds = %19
  %31 = load ptr, ptr %3, align 4
  %32 = getelementptr inbounds %struct.B, ptr %31, i32 0, i32 1
  %33 = getelementptr [13 x i32], ptr %32, i32 0, i32 7
  %34 = load i32, ptr %33, align 4
  store i32 %34, ptr %2, align 4
  br label %90

35:                                               ; preds = %1
  %36 = load ptr, ptr %3, align 4
  %37 = getelementptr inbounds %struct.B, ptr %36, i32 0, i32 1
  %38 = getelementptr [13 x i32], ptr %37, i32 0, i32 0
  %39 = load i32, ptr %38, align 4
  store i32 %39, ptr %2, align 4
  br label %90

40:                                               ; preds = %1
  %41 = load ptr, ptr %3, align 4
  %42 = getelementptr inbounds %struct.B, ptr %41, i32 0, i32 1
  %43 = getelementptr [13 x i32], ptr %42, i32 0, i32 2
  %44 = load i32, ptr %43, align 4
  store i32 %44, ptr %2, align 4
  br label %90

45:                                               ; preds = %1
  %46 = load ptr, ptr %3, align 4
  %47 = getelementptr inbounds %struct.B, ptr %46, i32 0, i32 1
  %48 = getelementptr [13 x i32], ptr %47, i32 0, i32 4
  %49 = load i32, ptr %48, align 4
  %50 = icmp ne i32 %49, 0
  br i1 %50, label %51, label %56

51:                                               ; preds = %45
  %52 = load ptr, ptr %3, align 4
  %53 = getelementptr inbounds %struct.B, ptr %52, i32 0, i32 1
  %54 = getelementptr [13 x i32], ptr %53, i32 0, i32 4
  %55 = load i32, ptr %54, align 4
  store i32 %55, ptr %2, align 4
  br label %90

56:                                               ; preds = %45
  %57 = load ptr, ptr %3, align 4
  %58 = getelementptr inbounds %struct.B, ptr %57, i32 0, i32 1
  %59 = getelementptr [13 x i32], ptr %58, i32 0, i32 1
  %60 = load i32, ptr %59, align 4
  store i32 %60, ptr %2, align 4
  br label %90

61:                                               ; preds = %1
  %62 = load ptr, ptr %3, align 4
  %63 = getelementptr inbounds %struct.B, ptr %62, i32 0, i32 1
  %64 = getelementptr [13 x i32], ptr %63, i32 0, i32 8
  %65 = load i32, ptr %64, align 4
  %66 = mul i32 %65, 4
  store i32 %66, ptr %2, align 4
  br label %90

67:                                               ; preds = %1
  %68 = load ptr, ptr %3, align 4
  %69 = getelementptr inbounds %struct.B, ptr %68, i32 0, i32 1
  %70 = getelementptr [13 x i32], ptr %69, i32 0, i32 12
  %71 = load i32, ptr %70, align 4
  store i32 %71, ptr %2, align 4
  br label %90

72:                                               ; preds = %1
  %73 = load ptr, ptr %3, align 4
  %74 = getelementptr inbounds %struct.B, ptr %73, i32 0, i32 1
  %75 = getelementptr [13 x i32], ptr %74, i32 0, i32 9
  %76 = load i32, ptr %75, align 4
  store i32 %76, ptr %2, align 4
  br label %90

77:                                               ; preds = %1
  %78 = load ptr, ptr %3, align 4
  %79 = getelementptr inbounds %struct.B, ptr %78, i32 0, i32 1
  %80 = getelementptr [13 x i32], ptr %79, i32 0, i32 10
  %81 = load i32, ptr %80, align 4
  %82 = mul i32 %81, 4
  store i32 %82, ptr %2, align 4
  br label %90

83:                                               ; preds = %1, %1, %1, %1, %1
  %84 = load ptr, ptr %3, align 4
  %85 = getelementptr inbounds %struct.B, ptr %84, i32 0, i32 1
  %86 = getelementptr [13 x i32], ptr %85, i32 0, i32 11
  %87 = load i32, ptr %86, align 4
  store i32 %87, ptr %2, align 4
  br label %90

88:                                               ; preds = %1
  %89 = load i32, ptr %4, align 4
  store i32 %89, ptr %2, align 4
  br label %90

90:                                               ; preds = %88, %83, %77, %72, %67, %61, %56, %51, %40, %35, %30, %25, %14, %9
  %91 = load i32, ptr %2, align 4
  ret i32 %91
}

attributes #0 = { noinline nounwind optnone uwtable "frame-pointer"="all" "no-builtins" "no-trapping-math"="true" "stack-protector-buffer-size"="8" "target-cpu"="generic" "target-features"="+bulk-memory,+extended-const,+multivalue,+mutable-globals,+nontrapping-fptoint,+sign-ext,-atomics,-exception-handling,-half-precision,-multimemory,-reference-types,-relaxed-simd,-simd128,-tail-call" }

!llvm.module.flags = !{!0, !1, !2, !3}
!llvm.ident = !{!4}

!0 = !{i32 7, !"Dwarf Version", i32 4}
!1 = !{i32 1, !"wchar_size", i32 4}
!2 = !{i32 7, !"uwtable", i32 2}
!3 = !{i32 7, !"frame-pointer", i32 2}
!4 = !{!"clang version 19.1.7 (https://github.com/ziglang/zig-bootstrap de1b01a8c1dddf75a560123ac1c2ab182b4830da)"}
