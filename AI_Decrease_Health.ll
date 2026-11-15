; ModuleID = '/tmp/claude-501/tb/src/AI_Decrease_Health.c'
source_filename = "/tmp/claude-501/tb/src/AI_Decrease_Health.c"
target datalayout = "e-m:e-p:32:32-p10:8:8-p20:8:8-i64:64-n32:64-S128-ni:1:10:20"
target triple = "wasm32-unknown-unknown-unknown"

%struct.CEngineApp_t = type { [3 x %struct.CFrameData_t], ptr, ptr, %struct.CScene_t, %struct.COnScreen_t, %struct.CBossesStatus_t, i32, ptr, %struct.CBossVars_t, %struct.CCamera_t, %struct.CFxSystem_t, %struct.CPause_t, %struct.COption_t, %struct.CKeys_t, %struct.CLoad_t, %struct.CSave_t, %struct.CRequestor_t, %struct.CMessageBox_t, %struct.CCheatCode_t, %struct.CGiveCheatCode_t, %struct.CCheat_t, %struct.CGInst_t, %struct.CGamePak_t, i32, i32, i32, i32, i32, i32, i32, i8, i8, i8, i8, i8, float, float, float, float, float, float, float, float, float, float, float, %struct.CQuatern_t, %struct.CQuatern_t, %struct.CQuatern_t, [4 x [4 x float]], [4 x [4 x float]], [5 x %struct.CVector3_t], ptr, ptr, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, float, i16, float, float, float, float, float, float, float, i32, i32, i32, %struct.CTraining_t, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i8, i8, i8, i8, i32, float, [4 x i8], [4 x i8], [4 x i8], float, float, float, float, float, float, float, float, i32, i32, i32, i32, i32, i32, i32, i32, float, float, [4 x i8], i32, float, i32, %struct.CROMWarpPoint_t, i32, %struct.CROMWarpPoint_t, i32, float, float, float, float, float, float, float, float, float, float, %struct.CVector3_t, i32, i32, %struct.CQuatern_t, i32, i32, float, float, float, float, float, float, float, float, i32, i32, ptr, i32, i8, %struct.OSContPad }
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
%struct.CVector3_t = type { float, float, float }
%struct.CQuatern_t = type { float, float, float, float }
%struct.OSContPad = type { i16, i8, i8, i8 }
%struct.CTMove = type { float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i8, i8, i8, i8, i8, i8, i8, i8, i8, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, float, i32, %struct.CVector3_t, %struct.CVector3_t, %struct.CQuatern_t, %struct.CQuatern_t, ptr, ptr, i8, i8, float, ptr }
%struct.CGameObjectInstance_t = type { %struct.CAnimInstanceHdr_t, %struct.CVector3_t, %struct.CQuatern_t, %struct.CQuatern_t, %struct.CQuatern_t, float, float, float, [2 x %union.Mtx], %struct.CGeometry_t, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, i16, i16, i16, i16, %struct.CBoundsRect_t, i32, %struct.CGameAnimateState_t, %struct.CGameAnimateState_t, i16, i16, i16, i16, float, float, float, float, ptr, ptr, ptr, i8, i8, i16, float, float, float, float, float, ptr, %struct.CAIDynamic_t, ptr, ptr }
%struct.CBoundsRect_t = type { float, float, float, float }
%struct.CGameAnimateState_t = type { ptr, i32, i32, i32, float, i32 }
%struct.CBoss_t = type { i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, i32, float, i32, i32, float, i32, i32, i32, i32, i32, %struct.CCollisionInfo2_t, float, float, float, float, float, float, %struct.CVector3_t, %struct.CVector3_t, %struct.CVector3_t, i16, i16, float, float, float, %struct.CVector3_t, float, float, float, float, float, float, float, float, float, float, float, float, float, float, float, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr, ptr }
%struct.CShield_t = type { i16, i16, i16, i16, ptr, float, float, ptr, ptr, ptr, i32 }
%struct.CEnemyAttributes_t = type { float, float, float, float, float, float, float, float, float, float, float, float, float, i32, i32, i32, i16, i16, i16, i16, i16, i8, i8, i8, i8, i8, i8 }

@D_800F9F10 = external global %struct.CEngineApp_t, align 8
@D_80128C20 = external global %struct.CTMove, align 4
@D_800F5584 = external constant float, align 4
@D_800F5588 = external constant double, align 8

; Function Attrs: noinline nounwind optnone uwtable
define hidden void @AI_Decrease_Health(ptr noundef %0, i32 noundef %1, i32 noundef %2, i32 noundef %3) #0 {
  %5 = alloca ptr, align 4
  %6 = alloca i32, align 4
  %7 = alloca i32, align 4
  %8 = alloca i32, align 4
  %9 = alloca i32, align 4
  %10 = alloca i32, align 4
  %11 = alloca i32, align 4
  %12 = alloca i32, align 4
  %13 = alloca i32, align 4
  store ptr %0, ptr %5, align 4
  store i32 %1, ptr %6, align 4
  store i32 %2, ptr %7, align 4
  store i32 %3, ptr %8, align 4
  %14 = load ptr, ptr %5, align 4
  %15 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %14, i32 0, i32 46
  %16 = getelementptr inbounds %struct.CAIDynamic_t, ptr %15, i32 0, i32 21
  %17 = load i32, ptr %16, align 4
  %18 = icmp ne i32 %17, 0
  br i1 %18, label %19, label %69

19:                                               ; preds = %4
  %20 = load ptr, ptr %5, align 4
  %21 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %20, i32 0, i32 34
  %22 = load ptr, ptr %21, align 4
  %23 = icmp ne ptr %22, null
  br i1 %23, label %24, label %68

24:                                               ; preds = %19
  %25 = load ptr, ptr %5, align 4
  %26 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %25, i32 0, i32 34
  %27 = load ptr, ptr %26, align 4
  %28 = getelementptr inbounds %struct.CBoss_t, ptr %27, i32 0, i32 51
  %29 = load ptr, ptr %28, align 4
  %30 = icmp ne ptr %29, null
  br i1 %30, label %31, label %68

31:                                               ; preds = %24
  %32 = load ptr, ptr %5, align 4
  %33 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %32, i32 0, i32 34
  %34 = load ptr, ptr %33, align 4
  %35 = getelementptr inbounds %struct.CBoss_t, ptr %34, i32 0, i32 51
  %36 = load ptr, ptr %35, align 4
  %37 = call i32 @CShield__IsOn(ptr noundef %36) #3
  %38 = icmp ne i32 %37, 0
  br i1 %38, label %39, label %68

39:                                               ; preds = %31
  %40 = load i32, ptr %6, align 4
  %41 = load ptr, ptr %5, align 4
  %42 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %41, i32 0, i32 34
  %43 = load ptr, ptr %42, align 4
  %44 = getelementptr inbounds %struct.CBoss_t, ptr %43, i32 0, i32 51
  %45 = load ptr, ptr %44, align 4
  %46 = getelementptr inbounds %struct.CShield_t, ptr %45, i32 0, i32 0
  %47 = load i16, ptr %46, align 4
  %48 = sext i16 %47 to i32
  %49 = sub i32 %48, %40
  %50 = trunc i32 %49 to i16
  store i16 %50, ptr %46, align 4
  %51 = load ptr, ptr %5, align 4
  %52 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %51, i32 0, i32 34
  %53 = load ptr, ptr %52, align 4
  %54 = getelementptr inbounds %struct.CBoss_t, ptr %53, i32 0, i32 51
  %55 = load ptr, ptr %54, align 4
  %56 = getelementptr inbounds %struct.CShield_t, ptr %55, i32 0, i32 0
  %57 = load i16, ptr %56, align 4
  %58 = sext i16 %57 to i32
  %59 = icmp slt i32 %58, 0
  br i1 %59, label %60, label %67

60:                                               ; preds = %39
  %61 = load ptr, ptr %5, align 4
  %62 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %61, i32 0, i32 34
  %63 = load ptr, ptr %62, align 4
  %64 = getelementptr inbounds %struct.CBoss_t, ptr %63, i32 0, i32 51
  %65 = load ptr, ptr %64, align 4
  %66 = getelementptr inbounds %struct.CShield_t, ptr %65, i32 0, i32 0
  store i16 0, ptr %66, align 4
  br label %67

67:                                               ; preds = %60, %39
  br label %68

68:                                               ; preds = %67, %31, %24, %19
  br label %410

69:                                               ; preds = %4
  %70 = load ptr, ptr %5, align 4
  %71 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %70, i32 0, i32 46
  %72 = getelementptr inbounds %struct.CAIDynamic_t, ptr %71, i32 0, i32 15
  %73 = load i16, ptr %72, align 4
  %74 = sext i16 %73 to i32
  %75 = icmp eq i32 %74, 0
  br i1 %75, label %76, label %77

76:                                               ; preds = %69
  br label %410

77:                                               ; preds = %69
  %78 = load ptr, ptr %5, align 4
  %79 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %78, i32 0, i32 46
  %80 = getelementptr inbounds %struct.CAIDynamic_t, ptr %79, i32 0, i32 12
  %81 = load i32, ptr %80, align 4
  %82 = and i32 %81, 1
  %83 = icmp ne i32 %82, 0
  br i1 %83, label %84, label %85

84:                                               ; preds = %77
  br label %410

85:                                               ; preds = %77
  %86 = load ptr, ptr %5, align 4
  %87 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %86, i32 0, i32 46
  %88 = getelementptr inbounds %struct.CAIDynamic_t, ptr %87, i32 0, i32 11
  %89 = load i32, ptr %88, align 4
  %90 = and i32 %89, 16777216
  %91 = icmp ne i32 %90, 0
  br i1 %91, label %92, label %93

92:                                               ; preds = %85
  br label %410

93:                                               ; preds = %85
  %94 = load ptr, ptr %5, align 4
  %95 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %94, i32 0, i32 46
  %96 = getelementptr inbounds %struct.CAIDynamic_t, ptr %95, i32 0, i32 11
  %97 = load i32, ptr %96, align 4
  %98 = and i32 %97, 67108864
  %99 = icmp ne i32 %98, 0
  br i1 %99, label %100, label %101

100:                                              ; preds = %93
  br label %410

101:                                              ; preds = %93
  %102 = load ptr, ptr %5, align 4
  %103 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %102, i32 0, i32 23
  %104 = load i32, ptr %103, align 8
  %105 = and i32 %104, 1
  %106 = icmp ne i32 %105, 0
  br i1 %106, label %107, label %108

107:                                              ; preds = %101
  br label %410

108:                                              ; preds = %101
  %109 = load ptr, ptr getelementptr inbounds (%struct.CScene_t, ptr getelementptr inbounds (%struct.CEngineApp_t, ptr @D_800F9F10, i32 0, i32 3), i32 0, i32 43), align 4
  %110 = load ptr, ptr %5, align 4
  %111 = icmp eq ptr %109, %110
  br i1 %111, label %112, label %315

112:                                              ; preds = %108
  %113 = call i32 @CCamera__InCinemaMode(ptr noundef getelementptr inbounds (%struct.CEngineApp_t, ptr @D_800F9F10, i32 0, i32 9)) #3
  %114 = icmp ne i32 %113, 0
  br i1 %114, label %115, label %116

115:                                              ; preds = %112
  br label %410

116:                                              ; preds = %112
  %117 = load i32, ptr getelementptr inbounds (%struct.CEngineApp_t, ptr @D_800F9F10, i32 0, i32 25), align 4
  %118 = and i32 %117, 1
  %119 = icmp ne i32 %118, 0
  br i1 %119, label %123, label %120

120:                                              ; preds = %116
  %121 = load i32, ptr getelementptr inbounds (%struct.CEngineApp_t, ptr @D_800F9F10, i32 0, i32 80), align 8
  %122 = icmp ne i32 %121, 0
  br i1 %122, label %123, label %124

123:                                              ; preds = %120, %116
  br label %410

124:                                              ; preds = %120
  %125 = load float, ptr getelementptr inbounds (%struct.CTMove, ptr @D_80128C20, i32 0, i32 196), align 4
  %126 = fcmp une float %125, 0.000000e+00
  br i1 %126, label %127, label %128

127:                                              ; preds = %124
  br label %410

128:                                              ; preds = %124
  %129 = call i32 @CEngineApp__GetPlayerWaterStatus(ptr noundef @D_800F9F10) #3
  store i32 %129, ptr %11, align 4
  %130 = load i32, ptr %11, align 4
  switch i32 %130, label %131 [
    i32 0, label %132
    i32 2, label %132
    i32 3, label %132
    i32 1, label %141
  ]

131:                                              ; preds = %128
  br label %132

132:                                              ; preds = %128, %128, %128, %131
  %133 = call i32 @RandomSwapWord() #3
  %134 = srem i32 %133, 4
  store i32 %134, ptr %13, align 4
  %135 = load i32, ptr %13, align 4
  switch i32 %135, label %140 [
    i32 0, label %136
    i32 1, label %137
    i32 2, label %138
    i32 3, label %139
  ]

136:                                              ; preds = %132
  store i32 395, ptr %12, align 4
  br label %140

137:                                              ; preds = %132
  store i32 396, ptr %12, align 4
  br label %140

138:                                              ; preds = %132
  store i32 397, ptr %12, align 4
  br label %140

139:                                              ; preds = %132
  store i32 398, ptr %12, align 4
  br label %140

140:                                              ; preds = %132, %139, %138, %137, %136
  br label %161

141:                                              ; preds = %128
  %142 = load i32, ptr getelementptr inbounds (%struct.CTMove, ptr @D_80128C20, i32 0, i32 157), align 4
  %143 = icmp ne i32 %142, 0
  br i1 %143, label %144, label %153

144:                                              ; preds = %141
  %145 = call i32 @RandomSwapWord() #3
  %146 = srem i32 %145, 4
  store i32 %146, ptr %13, align 4
  %147 = load i32, ptr %13, align 4
  switch i32 %147, label %152 [
    i32 0, label %148
    i32 1, label %149
    i32 2, label %150
    i32 3, label %151
  ]

148:                                              ; preds = %144
  store i32 395, ptr %12, align 4
  br label %152

149:                                              ; preds = %144
  store i32 396, ptr %12, align 4
  br label %152

150:                                              ; preds = %144
  store i32 397, ptr %12, align 4
  br label %152

151:                                              ; preds = %144
  store i32 398, ptr %12, align 4
  br label %152

152:                                              ; preds = %144, %151, %150, %149, %148
  br label %160

153:                                              ; preds = %141
  %154 = call i32 @RandomSwapWord() #3
  %155 = srem i32 %154, 2
  store i32 %155, ptr %13, align 4
  %156 = load i32, ptr %13, align 4
  switch i32 %156, label %159 [
    i32 0, label %157
    i32 1, label %158
  ]

157:                                              ; preds = %153
  store i32 399, ptr %12, align 4
  br label %159

158:                                              ; preds = %153
  store i32 400, ptr %12, align 4
  br label %159

159:                                              ; preds = %153, %158, %157
  br label %160

160:                                              ; preds = %159, %152
  br label %161

161:                                              ; preds = %160, %140
  %162 = load i32, ptr %8, align 4
  %163 = icmp ne i32 %162, 0
  br i1 %163, label %164, label %205

164:                                              ; preds = %161
  %165 = load i32, ptr getelementptr inbounds (%struct.CTMove, ptr @D_80128C20, i32 0, i32 109), align 4
  %166 = icmp eq i32 %165, 0
  br i1 %166, label %167, label %205

167:                                              ; preds = %164
  %168 = load i32, ptr getelementptr inbounds (%struct.CTMove, ptr @D_80128C20, i32 0, i32 159), align 4
  %169 = icmp ne i32 %168, 0
  br i1 %169, label %170, label %177

170:                                              ; preds = %167
  %171 = load float, ptr getelementptr inbounds (%struct.CTMove, ptr @D_80128C20, i32 0, i32 62), align 4
  %172 = fpext float %171 to double
  %173 = fcmp oge double %172, 5.000000e-01
  br i1 %173, label %174, label %176

174:                                              ; preds = %170
  %175 = load float, ptr @D_800F5584, align 4
  store float %175, ptr getelementptr inbounds (%struct.CTMove, ptr @D_80128C20, i32 0, i32 62), align 4
  store i32 0, ptr getelementptr inbounds (%struct.CTMove, ptr @D_80128C20, i32 0, i32 159), align 4
  br label %176

176:                                              ; preds = %174, %170
  br label %204

177:                                              ; preds = %167
  %178 = load float, ptr getelementptr inbounds (%struct.CTMove, ptr @D_80128C20, i32 0, i32 62), align 4
  %179 = fpext float %178 to double
  %180 = load double, ptr @D_800F5588, align 8
  %181 = fcmp olt double %179, %180
  br i1 %181, label %182, label %197

182:                                              ; preds = %177
  store float 0.000000e+00, ptr getelementptr inbounds (%struct.CTMove, ptr @D_80128C20, i32 0, i32 62), align 4
  store i32 1, ptr getelementptr inbounds (%struct.CTMove, ptr @D_80128C20, i32 0, i32 159), align 4
  %183 = load i32, ptr %11, align 4
  switch i32 %183, label %196 [
    i32 0, label %184
    i32 2, label %184
    i32 3, label %184
    i32 1, label %190
  ]

184:                                              ; preds = %182, %182, %182
  %185 = load ptr, ptr %5, align 4
  %186 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %185, i32 0, i32 0
  %187 = getelementptr inbounds %struct.CAnimInstanceHdr_t, ptr %186, i32 0, i32 0
  %188 = load i32, ptr %12, align 4
  %189 = call i32 @func_8005EED0(ptr noundef %187, i32 noundef %188, float noundef 1.000000e+00, i32 noundef 0) #3
  br label %196

190:                                              ; preds = %182
  %191 = load ptr, ptr %5, align 4
  %192 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %191, i32 0, i32 0
  %193 = getelementptr inbounds %struct.CAnimInstanceHdr_t, ptr %192, i32 0, i32 0
  %194 = load i32, ptr %12, align 4
  %195 = call i32 @func_8005EED0(ptr noundef %193, i32 noundef %194, float noundef 1.000000e+00, i32 noundef 0) #3
  br label %196

196:                                              ; preds = %182, %190, %184
  br label %203

197:                                              ; preds = %177
  %198 = load ptr, ptr %5, align 4
  %199 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %198, i32 0, i32 0
  %200 = getelementptr inbounds %struct.CAnimInstanceHdr_t, ptr %199, i32 0, i32 0
  %201 = load i32, ptr %12, align 4
  %202 = call i32 @func_8005EED0(ptr noundef %200, i32 noundef %201, float noundef 1.000000e+00, i32 noundef 0) #3
  store float 0.000000e+00, ptr getelementptr inbounds (%struct.CTMove, ptr @D_80128C20, i32 0, i32 62), align 4
  br label %203

203:                                              ; preds = %197, %196
  br label %204

204:                                              ; preds = %203, %176
  br label %205

205:                                              ; preds = %204, %164, %161
  %206 = load i32, ptr getelementptr inbounds (%struct.CTMove, ptr @D_80128C20, i32 0, i32 109), align 4
  %207 = icmp ne i32 %206, 0
  br i1 %207, label %208, label %249

208:                                              ; preds = %205
  %209 = load i32, ptr %6, align 4
  %210 = load i32, ptr getelementptr inbounds (%struct.CTMove, ptr @D_80128C20, i32 0, i32 93), align 4
  %211 = sub i32 %210, %209
  store i32 %211, ptr getelementptr inbounds (%struct.CTMove, ptr @D_80128C20, i32 0, i32 93), align 4
  %212 = load i32, ptr %7, align 4
  %213 = icmp ne i32 %212, 0
  br i1 %213, label %214, label %241

214:                                              ; preds = %208
  %215 = load i32, ptr %6, align 4
  %216 = mul i32 255, %215
  %217 = sdiv i32 %216, 20
  store i32 %217, ptr %9, align 4
  %218 = load i32, ptr %9, align 4
  %219 = icmp slt i32 255, %218
  br i1 %219, label %220, label %221

220:                                              ; preds = %214
  br label %223

221:                                              ; preds = %214
  %222 = load i32, ptr %9, align 4
  br label %223

223:                                              ; preds = %221, %220
  %224 = phi i32 [ 255, %220 ], [ %222, %221 ]
  %225 = icmp sgt i32 128, %224
  br i1 %225, label %226, label %227

226:                                              ; preds = %223
  br label %235

227:                                              ; preds = %223
  %228 = load i32, ptr %9, align 4
  %229 = icmp slt i32 255, %228
  br i1 %229, label %230, label %231

230:                                              ; preds = %227
  br label %233

231:                                              ; preds = %227
  %232 = load i32, ptr %9, align 4
  br label %233

233:                                              ; preds = %231, %230
  %234 = phi i32 [ 255, %230 ], [ %232, %231 ]
  br label %235

235:                                              ; preds = %233, %226
  %236 = phi i32 [ 128, %226 ], [ %234, %233 ]
  store i32 %236, ptr %9, align 4
  store i32 30, ptr %10, align 4
  %237 = load i32, ptr %9, align 4
  %238 = trunc i32 %237 to i8
  %239 = load i32, ptr %10, align 4
  %240 = trunc i32 %239 to i8
  call void @CEngineApp__DoColorFlash(ptr noundef @D_800F9F10, i8 noundef zeroext %238, i8 noundef zeroext %240, i32 noundef 255, i32 noundef 255, i32 noundef 0) #3
  br label %241

241:                                              ; preds = %235, %208
  %242 = load i32, ptr getelementptr inbounds (%struct.CTMove, ptr @D_80128C20, i32 0, i32 93), align 4
  %243 = icmp sle i32 %242, 3
  br i1 %243, label %244, label %247

244:                                              ; preds = %241
  %245 = load i32, ptr getelementptr inbounds (%struct.CTMove, ptr @D_80128C20, i32 0, i32 93), align 4
  %246 = sub i32 3, %245
  store i32 %246, ptr %6, align 4
  store i32 0, ptr getelementptr inbounds (%struct.CTMove, ptr @D_80128C20, i32 0, i32 109), align 4
  store i32 0, ptr getelementptr inbounds (%struct.CTMove, ptr @D_80128C20, i32 0, i32 93), align 4
  br label %248

247:                                              ; preds = %241
  store i32 0, ptr %6, align 4
  br label %248

248:                                              ; preds = %247, %244
  br label %249

249:                                              ; preds = %248, %205
  %250 = load i32, ptr %6, align 4
  %251 = icmp ne i32 %250, 0
  br i1 %251, label %252, label %314

252:                                              ; preds = %249
  %253 = load ptr, ptr %5, align 4
  %254 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %253, i32 0, i32 46
  %255 = getelementptr inbounds %struct.CAIDynamic_t, ptr %254, i32 0, i32 15
  %256 = load i16, ptr %255, align 4
  %257 = sext i16 %256 to i32
  %258 = load i32, ptr %6, align 4
  %259 = sub i32 %257, %258
  %260 = icmp sgt i32 0, %259
  br i1 %260, label %261, label %262

261:                                              ; preds = %252
  br label %270

262:                                              ; preds = %252
  %263 = load ptr, ptr %5, align 4
  %264 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %263, i32 0, i32 46
  %265 = getelementptr inbounds %struct.CAIDynamic_t, ptr %264, i32 0, i32 15
  %266 = load i16, ptr %265, align 4
  %267 = sext i16 %266 to i32
  %268 = load i32, ptr %6, align 4
  %269 = sub i32 %267, %268
  br label %270

270:                                              ; preds = %262, %261
  %271 = phi i32 [ 0, %261 ], [ %269, %262 ]
  %272 = trunc i32 %271 to i16
  %273 = load ptr, ptr %5, align 4
  %274 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %273, i32 0, i32 46
  %275 = getelementptr inbounds %struct.CAIDynamic_t, ptr %274, i32 0, i32 15
  store i16 %272, ptr %275, align 4
  %276 = load i32, ptr %7, align 4
  %277 = icmp ne i32 %276, 0
  br i1 %277, label %278, label %313

278:                                              ; preds = %270
  %279 = load ptr, ptr %5, align 4
  %280 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %279, i32 0, i32 46
  %281 = getelementptr inbounds %struct.CAIDynamic_t, ptr %280, i32 0, i32 15
  %282 = load i16, ptr %281, align 4
  %283 = icmp ne i16 %282, 0
  br i1 %283, label %284, label %307

284:                                              ; preds = %278
  %285 = load i32, ptr %6, align 4
  %286 = mul i32 255, %285
  %287 = sdiv i32 %286, 20
  store i32 %287, ptr %9, align 4
  %288 = load i32, ptr %9, align 4
  %289 = icmp slt i32 255, %288
  br i1 %289, label %290, label %291

290:                                              ; preds = %284
  br label %293

291:                                              ; preds = %284
  %292 = load i32, ptr %9, align 4
  br label %293

293:                                              ; preds = %291, %290
  %294 = phi i32 [ 255, %290 ], [ %292, %291 ]
  %295 = icmp sgt i32 128, %294
  br i1 %295, label %296, label %297

296:                                              ; preds = %293
  br label %305

297:                                              ; preds = %293
  %298 = load i32, ptr %9, align 4
  %299 = icmp slt i32 255, %298
  br i1 %299, label %300, label %301

300:                                              ; preds = %297
  br label %303

301:                                              ; preds = %297
  %302 = load i32, ptr %9, align 4
  br label %303

303:                                              ; preds = %301, %300
  %304 = phi i32 [ 255, %300 ], [ %302, %301 ]
  br label %305

305:                                              ; preds = %303, %296
  %306 = phi i32 [ 128, %296 ], [ %304, %303 ]
  store i32 %306, ptr %9, align 4
  store i32 30, ptr %10, align 4
  br label %308

307:                                              ; preds = %278
  store i32 255, ptr %9, align 4
  store i32 12, ptr %10, align 4
  br label %308

308:                                              ; preds = %307, %305
  %309 = load i32, ptr %9, align 4
  %310 = trunc i32 %309 to i8
  %311 = load i32, ptr %10, align 4
  %312 = trunc i32 %311 to i8
  call void @CEngineApp__DoFlash(ptr noundef @D_800F9F10, i8 noundef zeroext %310, i8 noundef zeroext %312) #3
  br label %313

313:                                              ; preds = %308, %270
  br label %314

314:                                              ; preds = %313, %249
  br label %347

315:                                              ; preds = %108
  %316 = load i32, ptr %6, align 4
  %317 = load ptr, ptr %5, align 4
  %318 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %317, i32 0, i32 46
  %319 = getelementptr inbounds %struct.CAIDynamic_t, ptr %318, i32 0, i32 15
  %320 = load i16, ptr %319, align 4
  %321 = sext i16 %320 to i32
  %322 = sub i32 %321, %316
  %323 = trunc i32 %322 to i16
  store i16 %323, ptr %319, align 4
  %324 = load ptr, ptr %5, align 4
  %325 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %324, i32 0, i32 46
  %326 = getelementptr inbounds %struct.CAIDynamic_t, ptr %325, i32 0, i32 15
  %327 = load i16, ptr %326, align 4
  %328 = sext i16 %327 to i32
  %329 = icmp ne i32 %328, 0
  br i1 %329, label %330, label %346

330:                                              ; preds = %315
  %331 = load ptr, ptr %5, align 4
  %332 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %331, i32 0, i32 0
  %333 = getelementptr inbounds %struct.CAnimInstanceHdr_t, ptr %332, i32 0, i32 0
  %334 = getelementptr inbounds %struct.CInstanceHdr_t, ptr %333, i32 0, i32 6
  %335 = load ptr, ptr %334, align 8
  %336 = getelementptr inbounds %struct.CEnemyAttributes_t, ptr %335, i32 0, i32 15
  %337 = load i32, ptr %336, align 4
  %338 = and i32 %337, 524288
  %339 = icmp ne i32 %338, 0
  br i1 %339, label %340, label %346

340:                                              ; preds = %330
  %341 = load ptr, ptr %5, align 4
  %342 = load ptr, ptr %5, align 4
  %343 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %342, i32 0, i32 0
  %344 = getelementptr inbounds %struct.CAnimInstanceHdr_t, ptr %343, i32 0, i32 0
  %345 = getelementptr inbounds %struct.CInstanceHdr_t, ptr %344, i32 0, i32 3
  call void @func_80063DE8(ptr noundef %341, ptr noundef byval(%struct.CVector3_t) align 4 %345) #3
  br label %346

346:                                              ; preds = %340, %330, %315
  br label %347

347:                                              ; preds = %346, %314
  %348 = load ptr, ptr %5, align 4
  %349 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %348, i32 0, i32 46
  %350 = getelementptr inbounds %struct.CAIDynamic_t, ptr %349, i32 0, i32 15
  %351 = load i16, ptr %350, align 4
  %352 = sext i16 %351 to i32
  %353 = icmp sle i32 %352, 0
  br i1 %353, label %354, label %410

354:                                              ; preds = %347
  %355 = load ptr, ptr %5, align 4
  %356 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %355, i32 0, i32 46
  %357 = getelementptr inbounds %struct.CAIDynamic_t, ptr %356, i32 0, i32 15
  store i16 0, ptr %357, align 4
  %358 = load ptr, ptr %5, align 4
  %359 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %358, i32 0, i32 46
  %360 = getelementptr inbounds %struct.CAIDynamic_t, ptr %359, i32 0, i32 11
  %361 = load i32, ptr %360, align 4
  %362 = and i32 %361, -4097
  store i32 %362, ptr %360, align 4
  %363 = load ptr, ptr %5, align 4
  %364 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %363, i32 0, i32 46
  %365 = getelementptr inbounds %struct.CAIDynamic_t, ptr %364, i32 0, i32 11
  %366 = load i32, ptr %365, align 4
  %367 = and i32 %366, -17
  store i32 %367, ptr %365, align 4
  %368 = load ptr, ptr %5, align 4
  %369 = call i32 @AI_CanWeRemoveIt(ptr noundef %368) #3
  %370 = icmp ne i32 %369, 0
  br i1 %370, label %371, label %381

371:                                              ; preds = %354
  %372 = load ptr, ptr getelementptr inbounds (%struct.CScene_t, ptr getelementptr inbounds (%struct.CEngineApp_t, ptr @D_800F9F10, i32 0, i32 3), i32 0, i32 43), align 4
  %373 = load ptr, ptr %5, align 4
  %374 = icmp ne ptr %372, %373
  br i1 %374, label %375, label %381

375:                                              ; preds = %371
  %376 = load ptr, ptr %5, align 4
  %377 = call i32 @AI_DoesItRegenerate(ptr noundef %376) #3
  %378 = icmp ne i32 %377, 0
  br i1 %378, label %381, label %379

379:                                              ; preds = %375
  %380 = load ptr, ptr %5, align 4
  call void @func_80034900(ptr noundef getelementptr inbounds (%struct.CEngineApp_t, ptr @D_800F9F10, i32 0, i32 3), ptr noundef %380, i32 noundef 1) #3
  br label %381

381:                                              ; preds = %379, %375, %371, %354
  %382 = load ptr, ptr %5, align 4
  %383 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %382, i32 0, i32 0
  %384 = getelementptr inbounds %struct.CAnimInstanceHdr_t, ptr %383, i32 0, i32 0
  %385 = getelementptr inbounds %struct.CInstanceHdr_t, ptr %384, i32 0, i32 6
  %386 = load ptr, ptr %385, align 8
  %387 = getelementptr inbounds %struct.CEnemyAttributes_t, ptr %386, i32 0, i32 19
  %388 = load i16, ptr %387, align 2
  %389 = icmp ne i16 %388, 0
  br i1 %389, label %390, label %409

390:                                              ; preds = %381
  %391 = load ptr, ptr %5, align 4
  %392 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %391, i32 0, i32 0
  %393 = getelementptr inbounds %struct.CAnimInstanceHdr_t, ptr %392, i32 0, i32 0
  %394 = load ptr, ptr %5, align 4
  %395 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %394, i32 0, i32 0
  %396 = getelementptr inbounds %struct.CAnimInstanceHdr_t, ptr %395, i32 0, i32 0
  %397 = load ptr, ptr %5, align 4
  %398 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %397, i32 0, i32 0
  %399 = getelementptr inbounds %struct.CAnimInstanceHdr_t, ptr %398, i32 0, i32 0
  %400 = getelementptr inbounds %struct.CInstanceHdr_t, ptr %399, i32 0, i32 3
  %401 = load ptr, ptr %5, align 4
  %402 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %401, i32 0, i32 0
  %403 = getelementptr inbounds %struct.CAnimInstanceHdr_t, ptr %402, i32 0, i32 0
  %404 = getelementptr inbounds %struct.CInstanceHdr_t, ptr %403, i32 0, i32 6
  %405 = load ptr, ptr %404, align 8
  %406 = getelementptr inbounds %struct.CEnemyAttributes_t, ptr %405, i32 0, i32 19
  %407 = load i16, ptr %406, align 2
  %408 = sitofp i16 %407 to float
  call void @AI_Event_Dispatcher(ptr noundef %393, ptr noundef %396, i32 noundef 231, i32 noundef -1, ptr noundef byval(%struct.CVector3_t) align 4 %400, float noundef %408) #3
  br label %409

409:                                              ; preds = %390, %381
  br label %410

410:                                              ; preds = %68, %76, %84, %92, %100, %107, %115, %123, %127, %409, %347
  ret void
}

declare i32 @CShield__IsOn(ptr noundef) #1

declare i32 @CCamera__InCinemaMode(ptr noundef) #1

declare i32 @CEngineApp__GetPlayerWaterStatus(ptr noundef) #1

declare i32 @RandomSwapWord(...) #2

declare i32 @func_8005EED0(ptr noundef, i32 noundef, float noundef, i32 noundef) #1

declare void @CEngineApp__DoColorFlash(ptr noundef, i8 noundef zeroext, i8 noundef zeroext, i32 noundef, i32 noundef, i32 noundef) #1

declare void @CEngineApp__DoFlash(ptr noundef, i8 noundef zeroext, i8 noundef zeroext) #1

declare void @func_80063DE8(ptr noundef, ptr noundef byval(%struct.CVector3_t) align 4) #1

declare i32 @AI_CanWeRemoveIt(ptr noundef) #1

; Function Attrs: noinline nounwind optnone uwtable
define internal i32 @AI_DoesItRegenerate(ptr noundef %0) #0 {
  %2 = alloca ptr, align 4
  store ptr %0, ptr %2, align 4
  %3 = load ptr, ptr %2, align 4
  %4 = getelementptr inbounds %struct.CGameObjectInstance_t, ptr %3, i32 0, i32 46
  %5 = getelementptr inbounds %struct.CAIDynamic_t, ptr %4, i32 0, i32 17
  %6 = load i16, ptr %5, align 4
  %7 = sext i16 %6 to i32
  ret i32 %7
}

declare void @func_80034900(ptr noundef, ptr noundef, i32 noundef) #1

declare void @AI_Event_Dispatcher(ptr noundef, ptr noundef, i32 noundef, i32 noundef, ptr noundef byval(%struct.CVector3_t) align 4, float noundef) #1

attributes #0 = { noinline nounwind optnone uwtable "frame-pointer"="all" "no-builtins" "no-trapping-math"="true" "stack-protector-buffer-size"="8" "target-cpu"="generic" "target-features"="+bulk-memory,+extended-const,+multivalue,+mutable-globals,+nontrapping-fptoint,+sign-ext,-atomics,-exception-handling,-half-precision,-multimemory,-reference-types,-relaxed-simd,-simd128,-tail-call" }
attributes #1 = { "frame-pointer"="all" "no-builtins" "no-trapping-math"="true" "stack-protector-buffer-size"="8" "target-cpu"="generic" "target-features"="+bulk-memory,+extended-const,+multivalue,+mutable-globals,+nontrapping-fptoint,+sign-ext,-atomics,-exception-handling,-half-precision,-multimemory,-reference-types,-relaxed-simd,-simd128,-tail-call" }
attributes #2 = { "frame-pointer"="all" "no-builtins" "no-prototype" "no-trapping-math"="true" "stack-protector-buffer-size"="8" "target-cpu"="generic" "target-features"="+bulk-memory,+extended-const,+multivalue,+mutable-globals,+nontrapping-fptoint,+sign-ext,-atomics,-exception-handling,-half-precision,-multimemory,-reference-types,-relaxed-simd,-simd128,-tail-call" }
attributes #3 = { nobuiltin "no-builtins" }

!llvm.module.flags = !{!0, !1, !2, !3}
!llvm.ident = !{!4}

!0 = !{i32 7, !"Dwarf Version", i32 4}
!1 = !{i32 1, !"wchar_size", i32 4}
!2 = !{i32 7, !"uwtable", i32 2}
!3 = !{i32 7, !"frame-pointer", i32 2}
!4 = !{!"clang version 19.1.7 (https://github.com/ziglang/zig-bootstrap de1b01a8c1dddf75a560123ac1c2ab182b4830da)"}
