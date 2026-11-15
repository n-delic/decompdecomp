; ModuleID = '/tmp/claude-501/tb/src/AI_Event_Dispatcher.c'
source_filename = "/tmp/claude-501/tb/src/AI_Event_Dispatcher.c"
target datalayout = "e-m:e-p:32:32-p10:8:8-p20:8:8-i64:64-n32:64-S128-ni:1:10:20"
target triple = "wasm32-unknown-unknown-unknown"

%struct.CVector3_t = type { float, float, float }
%struct.CEngineApp_t = type { [3 x %struct.CFrameData_t], ptr, ptr, %struct.CScene_t, %struct.COnScreen_t, %struct.CBossesStatus_t, i32, ptr, %struct.CBossVars_t, %struct.CCamera_t, %struct.CFxSystem_t, %struct.CPause_t, %struct.COption_t, %struct.CKeys_t, %struct.CLoad_t, %struct.CSave_t, %struct.CRequestor_t, %struct.CMessageBox_t, %struct.CCheatCode_t, %struct.CGiveCheatCode_t, %struct.CCheat_t, %struct.CGInst_t, %struct.CGamePak_t, i32, i32, i32, i32, i32, i32, i32, i8, i8, i8, i8, i8, float, float, float, float, float, float, float, float, float, float, float, %struct.CQuatern_t, %struct.CQuatern_t, %struct.CQuatern_t, [4 x [4 x float]], [4 x [4 x float]], [5 x %struct.CVector3_t], ptr, ptr, i32, i32, i32, i32, i32, i32, i32, i32, i32, float, i16, float, float, float, float, float, i32, float, float, i32, i32, i32, %struct.CTraining_t, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i8, i8, i8, i8, i32, float, [4 x i8], [4 x i8], [4 x i8], float, float, float, float, float, float, float, float, i32, i32, i32, i32, i32, i32, i32, i32, float, float, [4 x i8], i32, float, i32, %struct.CROMWarpPoint_t, i32, %struct.CROMWarpPoint_t, i32, float, float, float, float, float, float, float, float, float, float, %struct.CVector3_t, i32, i32, %struct.CQuatern_t, i32, i32, float, float, float, float, float, float, float, float, i32, i32, ptr, i32, i8, %struct.OSContPad }
%struct.CFrameData_t = type { %union.Mtx, %struct.LookAt, %struct.CSunFrameData_t, %struct.OSScTask_s, %struct.OSScTask_s, %union.GFXMsg, ptr, ptr, ptr, i32, i32, i32, i32 }
%union.Mtx = type { i64, [56 x i8] }
%struct.LookAt = type { [2 x %union.Light] }
%union.Light = type { [2 x i64] }
%struct.CSunFrameData_t = type { [2 x i64], i32, i32, i32, i32 }
%struct.OSScTask_s = type { ptr, i32, i32, ptr, %union.OSTask, ptr, ptr, i64, i64 }
%union.OSTask = type { i64, [56 x i8] }
%union.GFXMsg = type { %struct.OSScMsg }
%struct.OSScMsg = type { i16, [30 x i8] }
%struct.CScene_t = type { %struct.CCartCache_t, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, i32, i32, i32, i32, i32, [1024 x ptr], i32, [64 x ptr], i32, ptr, [64 x ptr], i32, i32, [64 x ptr], %struct.CParticleSystem_t, %struct.CSimplePool_t, %struct.CROMLevel_t, [4 x i16], %struct.CTextureLoader_t, i32, i32, i32, float, float, %struct.CQuatern_t, i32, i32, i32, %struct.CROMWarpPoint_t, float, i32, i32, i32, i32, %struct.CPickupMonitor_t }
%struct.CCartCache_t = type { %struct.CHashTable_t, ptr, ptr, ptr, ptr, i32, [512 x %struct.CCacheEntry_t], ptr, [16 x %struct.CNotifyEntry_t], ptr }
%struct.CHashTable_t = type { ptr, i32, i32, ptr, ptr, i32 }
%struct.CCacheEntry_t = type { ptr, i32, ptr, i32, ptr, i32, i32, i32, i32, ptr, ptr, ptr, ptr }
%struct.CNotifyEntry_t = type { ptr, ptr, ptr, ptr, ptr, i32, ptr, ptr, ptr, ptr }
%struct.CParticleSystem_t = type { [128 x %struct.CParticle_t], ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr }
%struct.CParticle_t = type { %struct.CAnimInstanceHdr_t, [2 x %union.Mtx], ptr, %struct.CTextureLoader_t, ptr, i32, float, float, float, i32, i32, i32, float, float, float, float, float, i8, i8, %struct.CCollisionInfo2_t, i16, [3 x i8], [3 x i8], ptr, ptr, ptr, ptr }
%struct.CAnimInstanceHdr_t = type { %struct.CInstanceHdr_t, %struct.CVector3_t }
%struct.CInstanceHdr_t = type { i8, i8, i16, %struct.CVector3_t, float, ptr, ptr }
%struct.CCollisionInfo2_t = type { i32, i32, i32, i32, float, float, float, float, float, ptr, ptr, %struct.CVector3_t, ptr, %struct.CVector3_t, i32, i32, ptr, %struct.CVector3_t, ptr, %struct.CVector3_t, %struct.CVector3_t }
%struct.CSimplePool_t = type { [32 x %struct.CDynamicSimple_t], ptr, ptr, ptr, ptr, ptr, ptr, i32 }
%struct.CDynamicSimple_t = type { %struct.CGameSimpleInstance_t, float, float, i32, ptr, ptr, ptr }
%struct.CGameSimpleInstance_t = type { %struct.CAnimInstanceHdr_t, ptr, ptr, ptr, [2 x %union.Mtx], [2 x %union.Mtx], %struct.CGeometry_t, %struct.CVector3_t, float, float, %struct.CROMBounds_t, float, float, i16, i16, float, float }
%struct.CGeometry_t = type { ptr, ptr }
%struct.CROMBounds_t = type { %struct.CVector3_t, %struct.CVector3_t }
%struct.CROMLevel_t = type { float, [3 x i8], [3 x i8], [3 x i8], [3 x i8], i8, [3 x i8] }
%struct.CTextureLoader_t = type { ptr, ptr, i32 }
%struct.CPickupMonitor_t = type { i32, [16 x %struct.CPickupGenerator_t], i32 }
%struct.CPickupGenerator_t = type { float, ptr, %struct.CVector3_t, ptr }
%struct.COnScreen_t = type { i16, i32, i32, i32, float, i32, float, float, i8, i32, i32, i32, i32, i32, i32, float, float, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, float, float, %struct.COverlay_t, %struct.COverlay_t, %struct.COverlay_t, %struct.COverlay_t, %struct.COverlay_t, %struct.COverlay_t, ptr, i8 }
%struct.COverlay_t = type { i32, ptr, i32, i32, float, float, float, float, float, float, float, float, float }
%struct.CBossesStatus_t = type { ptr, [3 x %struct.CBossData_t], [20 x %struct.CBossEvent_t], i32, i32, [20 x %struct.CBossPickup_t], i32 }
%struct.CBossData_t = type { ptr, ptr, ptr, %struct.CVector3_t, float, i32, i32, i32, i32, [800 x i8], %struct.CAIDynamic_t, i16, float, float }
%struct.CAIDynamic_t = type { float, float, float, float, float, float, float, float, float, float, float, i32, i32, i16, i16, i16, i16, i16, %struct.CVector3_t, %struct.CVector3_t, %struct.CVector3_t, i32, i32, i32, ptr, i16, i16, i16, i16 }
%struct.CBossEvent_t = type { ptr, %struct.CVector3_t, i32, float }
%struct.CBossPickup_t = type { i32, %struct.CVector3_t }
%struct.CBossVars_t = type { i32, i32, float, [4 x ptr], float, ptr, %struct.CVector3_t, %struct.CVector3_t }
%struct.CCamera_t = type { float, float, i16, float, i32, i32, i32, i32, i16, float, i16, float, i16, i16, float, float, %struct.CCameraView_t, float, %struct.CCameraView_t, %struct.CCameraView_t, %struct.CCameraView_t, %struct.CVector3_t, %struct.CVector3_t, ptr, ptr, %struct.CVector3_t, %struct.CVector3_t, float, float, float, float, float, float, float, float, %struct.CShake_t, %struct.CShake_t, %struct.CShake_t, %struct.CVector3_t, i32, float, %struct.CVector3_t, float, float, float, [8 x i32], i32 }
%struct.CCameraView_t = type { float, %struct.CVector3_t, %struct.CVector3_t, %struct.CVector3_t, float, float, float, float }
%struct.CShake_t = type { float, float, float, float }
%struct.CFxSystem_t = type { [2 x [400 x %union.Vtx]], i32, ptr, [150 x %struct.CSwooshEdge_t], %struct.CList_t, [32 x %struct.CSwoosh_t], %struct.CDynamicList_t, [3 x %struct.CTimerFx_t], %struct.CDynamicList_t }
%union.Vtx = type { i64, [8 x i8] }
%struct.CSwooshEdge_t = type { %struct.CNode_t, float, float, %union.Mtx }
%struct.CNode_t = type { ptr, ptr }
%struct.CList_t = type { ptr, ptr, i32 }
%struct.CSwoosh_t = type { %struct.CNode_t, i16, float, ptr, ptr, ptr, %struct.CList_t }
%struct.CTimerFx_t = type { %struct.CNode_t, float, float, i32, ptr, ptr }
%struct.CDynamicList_t = type { %struct.CList_t, %struct.CList_t }
%struct.CPause_t = type { i32, float, i32, i32 }
%struct.COption_t = type { i32, float, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32 }
%struct.CKeys_t = type { i32, float, i32 }
%struct.CLoad_t = type { float, i16, i32, i16, i32, i32, i32, i32, i32, i32, i32, i32 }
%struct.CSave_t = type { float, i16, i32, i16, i32, i32, i32, i32, i32, i32, i32 }
%struct.CRequestor_t = type { float, i16, i32, i32, ptr, ptr, ptr }
%struct.CMessageBox_t = type { float, i16, i32, i32, ptr, ptr }
%struct.CCheatCode_t = type { float, i16, i32, i32, i32, i32 }
%struct.CGiveCheatCode_t = type { float, i16, i32, ptr, i32 }
%struct.CCheat_t = type { i32, float, i32, i32, i32 }
%struct.CGInst_t = type { i32, float, i32, i32 }
%struct.CGamePak_t = type { float, i16, i32, i32, i32 }
%struct.CTraining_t = type { i32, float, i32 }
%struct.CROMWarpPoint_t = type { %struct.CVector3_t, float, i16, i16 }
%struct.CQuatern_t = type { float, float, float, float }
%struct.OSContPad = type { i16, i8, i8, i8 }
%struct.CUnindexedSet_t = type { ptr, i32, i32 }
%struct.CGameObjectInstance_t = type { %struct.CAnimInstanceHdr_t, %struct.CVector3_t, %struct.CQuatern_t, %struct.CQuatern_t, %struct.CQuatern_t, float, float, float, [2 x %union.Mtx], %struct.CGeometry_t, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, i16, i16, i16, i16, %struct.CBoundsRect_t, i32, %struct.CGameAnimateState_t, %struct.CGameAnimateState_t, i16, i16, i16, i16, float, float, float, float, ptr, ptr, ptr, i8, i8, i16, float, float, float, float, float, ptr, %struct.CAIDynamic_t, ptr, ptr }
%struct.CBoundsRect_t = type { float, float, float, float }
%struct.CGameAnimateState_t = type { ptr, i32, i32, i32, float, i32 }
%struct.CEnemyAttributes_t = type { float, float, float, float, float, float, float, float, float, float, float, float, float, i32, i32, i32, i16, i16, i16, i16, i16, i8, i8, i8, i8, i8, i8 }

@WallPositions = hidden global [5 x %struct.CVector3_t] [%struct.CVector3_t { float 0xC08CCCCCC0000000, float 0.000000e+00, float 0x408CCCCCC0000000 }, %struct.CVector3_t { float 0x408CCCCCC0000000, float 0.000000e+00, float 0x408CCCCCC0000000 }, %struct.CVector3_t { float 0x408CCCCCC0000000, float 0.000000e+00, float 0xC08CCCCCC0000000 }, %struct.CVector3_t { float 0xC08CCCCCC0000000, float 0.000000e+00, float 0xC08CCCCCC0000000 }, %struct.CVector3_t { float 0xC08CCCCCC0000000, float 0.000000e+00, float 0x408CCCCCC0000000 }], align 16
@D_800F9F10 = external global %struct.CEngineApp_t, align 8

; Function Attrs: noinline nounwind optnone uwtable
define hidden void @AI_Event_Dispatcher(ptr noundef %0, ptr noundef %1, i32 noundef %2, i32 noundef %3, ptr noundef byval(%struct.CVector3_t) align 4 %4, float noundef %5) #0 {
  %7 = alloca ptr, align 4
  %8 = alloca ptr, align 4
  %9 = alloca i32, align 4
  %10 = alloca i32, align 4
  %11 = alloca float, align 4
  %12 = alloca i32, align 4
  %13 = alloca i32, align 4
  %14 = alloca ptr, align 4
  %15 = alloca ptr, align 4
  %16 = alloca %struct.CUnindexedSet_t, align 4
  %17 = alloca ptr, align 4
  %18 = alloca i32, align 4
  store ptr %0, ptr %7, align 4
  store ptr %1, ptr %8, align 4
  store i32 %2, ptr %9, align 4
  store i32 %3, ptr %10, align 4
  store float %5, ptr %11, align 4
  %19 = load i32, ptr getelementptr inbounds (%struct.CEngineApp_t, ptr @D_800F9F10, i32 0, i32 80), align 8
  %20 = icmp ne i32 %19, 0
  br i1 %20, label %21, label %24

21:                                               ; preds = %6
  %22 = load i32, ptr getelementptr inbounds (%struct.CEngineApp_t, ptr @D_800F9F10, i32 0, i32 23), align 4
  %23 = icmp eq i32 %22, 4
  br label %24

24:                                               ; preds = %21, %6
  %25 = phi i1 [ false, %6 ], [ %23, %21 ]
  %26 = zext i1 %25 to i32
  store i32 %26, ptr %18, align 4
  %27 = load ptr, ptr getelementptr inbounds (%struct.CEngineApp_t, ptr @D_800F9F10, i32 0, i32 7), align 8
  %28 = icmp ne ptr %27, null
  br i1 %28, label %29, label %39

29:                                               ; preds = %24
  %30 = load i32, ptr %9, align 4
  %31 = icmp eq i32 %30, 232
  br i1 %31, label %35, label %32

32:                                               ; preds = %29
  %33 = load i32, ptr %9, align 4
  %34 = icmp eq i32 %33, 233
  br i1 %34, label %35, label %39

35:                                               ; preds = %32, %29
  %36 = load ptr, ptr %8, align 4
  %37 = load i32, ptr %9, align 4
  %38 = load float, ptr %11, align 4
  call void @CBossesStatus__SaveEvent(ptr noundef getelementptr inbounds (%struct.CEngineApp_t, ptr @D_800F9F10, i32 0, i32 5), ptr noundef %36, i32 noundef %37, ptr noundef byval(%struct.CVector3_t) align 4 %4, float noundef %38) #2
  br label %39

39:                                               ; preds = %35, %32, %24
  %40 = load i32, ptr %9, align 4
  %41 = call i32 @IsSingleEvent(i32 noundef %40) #2
  %42 = icmp ne i32 %41, 0
  br i1 %42, label %43, label %51

43:                                               ; preds = %39
  %44 = load i32, ptr %18, align 4
  %45 = icmp ne i32 %44, 0
  br i1 %45, label %51, label %46

46:                                               ; preds = %43
  %47 = load ptr, ptr %7, align 4
  %48 = load ptr, ptr %8, align 4
  %49 = load i32, ptr %9, align 4
  %50 = load float, ptr %11, align 4
  call void @AI_Single_Event(ptr noundef %47, ptr noundef %48, i32 noundef %49, ptr noundef byval(%struct.CVector3_t) align 4 %4, float noundef %50) #2
  br label %149

51:                                               ; preds = %43, %39
  store ptr @D_800F9F10, ptr %17, align 4
  %52 = load i32, ptr %9, align 4
  %53 = call i32 @SendEventToWholeWorld(i32 noundef %52) #2
  %54 = icmp ne i32 %53, 0
  br i1 %54, label %55, label %102

55:                                               ; preds = %51
  %56 = load ptr, ptr %17, align 4
  %57 = getelementptr inbounds %struct.CEngineApp_t, ptr %56, i32 0, i32 3
  %58 = getelementptr inbounds %struct.CScene_t, ptr %57, i32 0, i32 19
  %59 = load ptr, ptr %58, align 4
  %60 = call ptr @CCacheEntry__GetData(ptr noundef %59) #2
  call void @CUnindexedSet__ConstructFromRawData(ptr noundef %16, ptr noundef %60, i32 noundef 0) #2
  %61 = getelementptr inbounds %struct.CUnindexedSet_t, ptr %16, i32 0, i32 0
  %62 = load ptr, ptr %61, align 4
  %63 = getelementptr i8, ptr %62, i32 8
  store ptr %63, ptr %15, align 4
  %64 = getelementptr inbounds %struct.CUnindexedSet_t, ptr %16, i32 0, i32 0
  %65 = load ptr, ptr %64, align 4
  %66 = getelementptr i32, ptr %65, i32 1
  %67 = load i32, ptr %66, align 4
  store i32 %67, ptr %13, align 4
  store i32 0, ptr %12, align 4
  br label %68

68:                                               ; preds = %98, %55
  %69 = load i32, ptr %12, align 4
  %70 = load i32, ptr %13, align 4
  %71 = icmp slt i32 %69, %70
  br i1 %71, label %72, label %101

72:                                               ; preds = %68
  %73 = load ptr, ptr %15, align 4
  %74 = load i32, ptr %12, align 4
  %75 = getelementptr %struct.CGameObjectInstance_t, ptr %73, i32 %74
  store ptr %75, ptr %14, align 4
  %76 = load ptr, ptr %14, align 4
  %77 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %76, i32 0, i32 0
  %78 = getelementptr inbounds %struct.CAnimInstanceHdr_t, ptr %77, i32 0, i32 0
  %79 = load ptr, ptr %7, align 4
  %80 = icmp ne ptr %78, %79
  br i1 %80, label %81, label %97

81:                                               ; preds = %72
  %82 = load ptr, ptr %14, align 4
  %83 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %82, i32 0, i32 0
  %84 = getelementptr inbounds %struct.CAnimInstanceHdr_t, ptr %83, i32 0, i32 0
  %85 = getelementptr inbounds %struct.CInstanceHdr_t, ptr %84, i32 0, i32 6
  %86 = load ptr, ptr %85, align 8
  %87 = getelementptr inbounds %struct.CEnemyAttributes_t, ptr %86, i32 0, i32 13
  %88 = load i32, ptr %87, align 4
  %89 = load i32, ptr %10, align 4
  %90 = and i32 %88, %89
  %91 = icmp ne i32 %90, 0
  br i1 %91, label %92, label %97

92:                                               ; preds = %81
  %93 = load ptr, ptr %14, align 4
  %94 = load ptr, ptr %8, align 4
  %95 = load i32, ptr %9, align 4
  %96 = load float, ptr %11, align 4
  call void @func_800621A0(ptr noundef %93, ptr noundef %94, i32 noundef %95, ptr noundef byval(%struct.CVector3_t) align 4 %4, float noundef %96) #2
  br label %97

97:                                               ; preds = %92, %81, %72
  br label %98

98:                                               ; preds = %97
  %99 = load i32, ptr %12, align 4
  %100 = add i32 %99, 1
  store i32 %100, ptr %12, align 4
  br label %68, !llvm.loop !5

101:                                              ; preds = %68
  br label %148

102:                                              ; preds = %51
  %103 = load i32, ptr %18, align 4
  %104 = icmp ne i32 %103, 0
  br i1 %104, label %147, label %105

105:                                              ; preds = %102
  %106 = load ptr, ptr %17, align 4
  %107 = getelementptr inbounds %struct.CEngineApp_t, ptr %106, i32 0, i32 3
  %108 = getelementptr inbounds %struct.CScene_t, ptr %107, i32 0, i32 45
  %109 = load i32, ptr %108, align 8
  store i32 %109, ptr %13, align 4
  store i32 0, ptr %12, align 4
  br label %110

110:                                              ; preds = %143, %105
  %111 = load i32, ptr %12, align 4
  %112 = load i32, ptr %13, align 4
  %113 = icmp slt i32 %111, %112
  br i1 %113, label %114, label %146

114:                                              ; preds = %110
  %115 = load ptr, ptr %17, align 4
  %116 = getelementptr inbounds %struct.CEngineApp_t, ptr %115, i32 0, i32 3
  %117 = getelementptr inbounds %struct.CScene_t, ptr %116, i32 0, i32 44
  %118 = load i32, ptr %12, align 4
  %119 = getelementptr [64 x ptr], ptr %117, i32 0, i32 %118
  %120 = load ptr, ptr %119, align 4
  store ptr %120, ptr %14, align 4
  %121 = load ptr, ptr %14, align 4
  %122 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %121, i32 0, i32 0
  %123 = getelementptr inbounds %struct.CAnimInstanceHdr_t, ptr %122, i32 0, i32 0
  %124 = load ptr, ptr %7, align 4
  %125 = icmp ne ptr %123, %124
  br i1 %125, label %126, label %142

126:                                              ; preds = %114
  %127 = load ptr, ptr %14, align 4
  %128 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %127, i32 0, i32 0
  %129 = getelementptr inbounds %struct.CAnimInstanceHdr_t, ptr %128, i32 0, i32 0
  %130 = getelementptr inbounds %struct.CInstanceHdr_t, ptr %129, i32 0, i32 6
  %131 = load ptr, ptr %130, align 8
  %132 = getelementptr inbounds %struct.CEnemyAttributes_t, ptr %131, i32 0, i32 13
  %133 = load i32, ptr %132, align 4
  %134 = load i32, ptr %10, align 4
  %135 = and i32 %133, %134
  %136 = icmp ne i32 %135, 0
  br i1 %136, label %137, label %142

137:                                              ; preds = %126
  %138 = load ptr, ptr %14, align 4
  %139 = load ptr, ptr %8, align 4
  %140 = load i32, ptr %9, align 4
  %141 = load float, ptr %11, align 4
  call void @func_800621A0(ptr noundef %138, ptr noundef %139, i32 noundef %140, ptr noundef byval(%struct.CVector3_t) align 4 %4, float noundef %141) #2
  br label %142

142:                                              ; preds = %137, %126, %114
  br label %143

143:                                              ; preds = %142
  %144 = load i32, ptr %12, align 4
  %145 = add i32 %144, 1
  store i32 %145, ptr %12, align 4
  br label %110, !llvm.loop !7

146:                                              ; preds = %110
  br label %147

147:                                              ; preds = %146, %102
  br label %148

148:                                              ; preds = %147, %101
  br label %149

149:                                              ; preds = %148, %46
  ret void
}

declare void @CBossesStatus__SaveEvent(ptr noundef, ptr noundef, i32 noundef, ptr noundef byval(%struct.CVector3_t) align 4, float noundef) #1

declare i32 @IsSingleEvent(i32 noundef) #1

declare void @AI_Single_Event(ptr noundef, ptr noundef, i32 noundef, ptr noundef byval(%struct.CVector3_t) align 4, float noundef) #1

; Function Attrs: noinline nounwind optnone uwtable
define internal i32 @SendEventToWholeWorld(i32 noundef %0) #0 {
  %2 = alloca i32, align 4
  %3 = alloca i32, align 4
  store i32 %0, ptr %3, align 4
  %4 = load i32, ptr %3, align 4
  switch i32 %4, label %6 [
    i32 231, label %5
  ]

5:                                                ; preds = %1
  store i32 1, ptr %2, align 4
  br label %7

6:                                                ; preds = %1
  store i32 0, ptr %2, align 4
  br label %7

7:                                                ; preds = %6, %5
  %8 = load i32, ptr %2, align 4
  ret i32 %8
}

declare void @CUnindexedSet__ConstructFromRawData(ptr noundef, ptr noundef, i32 noundef) #1

declare ptr @CCacheEntry__GetData(ptr noundef) #1

declare void @func_800621A0(ptr noundef, ptr noundef, i32 noundef, ptr noundef byval(%struct.CVector3_t) align 4, float noundef) #1

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
!5 = distinct !{!5, !6}
!6 = !{!"llvm.loop.mustprogress"}
!7 = distinct !{!7, !6}
