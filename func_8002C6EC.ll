; ModuleID = '/private/tmp/claude-501/-Users-a--claude-spares-spare-a2hqyf/ee5497e5-bb0a-4d55-b497-be4e4fae1075/scratchpad/vr/out/src/func_8002C6EC.c'
source_filename = "/private/tmp/claude-501/-Users-a--claude-spares-spare-a2hqyf/ee5497e5-bb0a-4d55-b497-be4e4fae1075/scratchpad/vr/out/src/func_8002C6EC.c"
target datalayout = "e-m:e-p:32:32-p10:8:8-p20:8:8-i64:64-n32:64-S128-ni:1:10:20"
target triple = "wasm32-unknown-unknown-unknown"

%struct.Scratch = type { %struct.Vec3, i32, %struct.Vec3, i32, [3 x i16], i16, %struct.Matrix32, %struct.Matrix32, [6 x i16], i32, %union.Pair, %union.Pair, %union.Pair, [8 x i8], i32 }
%struct.Vec3 = type { i32, i32, i32 }
%struct.Matrix32 = type { [9 x i16], [14 x i8] }
%union.Pair = type { %struct.anon }
%struct.anon = type { i16, i16, i32 }

@D_8009E270 = external global [0 x i8], align 1
@D_8009E271 = external global [0 x i8], align 1
@D_8009E278 = external global [0 x i8], align 1
@D_8009E279 = external global [0 x i8], align 1
@D_8009E284 = external global [0 x i8], align 1
@D_8009E285 = external global [0 x i8], align 1

; Function Attrs: noinline nounwind optnone uwtable
define hidden zeroext i8 @func_8002C6EC(ptr noundef %0, ptr noundef %1) #0 {
  %3 = alloca i8, align 1
  %4 = alloca ptr, align 4
  %5 = alloca ptr, align 4
  %6 = alloca %struct.Scratch, align 4
  %7 = alloca ptr, align 4
  %8 = alloca ptr, align 4
  %9 = alloca ptr, align 4
  %10 = alloca i32, align 4
  %11 = alloca i32, align 4
  %12 = alloca ptr, align 4
  %13 = alloca ptr, align 4
  %14 = alloca i16, align 2
  %15 = alloca i32, align 4
  %16 = alloca i32, align 4
  %17 = alloca i32, align 4
  %18 = alloca i32, align 4
  %19 = alloca i32, align 4
  %20 = alloca i32, align 4
  %21 = alloca i32, align 4
  %22 = alloca i32, align 4
  %23 = alloca i32, align 4
  %24 = alloca i32, align 4
  %25 = alloca i32, align 4
  %26 = alloca ptr, align 4
  %27 = alloca ptr, align 4
  %28 = alloca ptr, align 4
  %29 = alloca ptr, align 4
  %30 = alloca ptr, align 4
  %31 = alloca ptr, align 4
  %32 = alloca ptr, align 4
  %33 = alloca ptr, align 4
  %34 = alloca ptr, align 4
  %35 = alloca i8, align 1
  %36 = alloca ptr, align 4
  %37 = alloca ptr, align 4
  %38 = alloca ptr, align 4
  %39 = alloca ptr, align 4
  %40 = alloca ptr, align 4
  %41 = alloca ptr, align 4
  %42 = alloca i8, align 1
  store ptr %0, ptr %4, align 4
  store ptr %1, ptr %5, align 4
  %43 = load ptr, ptr %5, align 4
  %44 = getelementptr i8, ptr %43, i32 92
  %45 = load volatile ptr, ptr %44, align 4
  store ptr %45, ptr %29, align 4
  %46 = load ptr, ptr %5, align 4
  %47 = getelementptr i8, ptr %46, i32 140
  %48 = load volatile ptr, ptr %47, align 4
  store ptr %48, ptr %26, align 4
  %49 = load ptr, ptr %4, align 4
  %50 = getelementptr i8, ptr %49, i32 140
  %51 = load ptr, ptr %50, align 4
  store ptr %51, ptr %27, align 4
  %52 = load ptr, ptr %26, align 4
  %53 = getelementptr i8, ptr %52, i32 260
  store ptr %53, ptr %8, align 4
  %54 = load ptr, ptr %27, align 4
  %55 = getelementptr i8, ptr %54, i32 824
  store ptr %55, ptr %9, align 4
  %56 = load ptr, ptr %26, align 4
  %57 = getelementptr i8, ptr %56, i32 256
  %58 = load i32, ptr %57, align 4
  store i32 %58, ptr %10, align 4
  %59 = load ptr, ptr %27, align 4
  %60 = getelementptr i8, ptr %59, i32 820
  %61 = load i32, ptr %60, align 4
  store i32 %61, ptr %11, align 4
  %62 = load ptr, ptr %29, align 4
  %63 = getelementptr i8, ptr %62, i32 64
  store ptr %63, ptr %28, align 4
  %64 = load ptr, ptr %28, align 4
  %65 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 7
  call void @func_8002DA98(ptr noundef %64, ptr noundef %65) #3
  %66 = load ptr, ptr %5, align 4
  %67 = getelementptr i8, ptr %66, i32 128
  %68 = load ptr, ptr %67, align 4
  %69 = getelementptr i8, ptr %68, i32 236
  %70 = load i32, ptr %69, align 4
  %71 = add i32 %70, 15
  %72 = trunc i32 %71 to i16
  %73 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 8
  %74 = getelementptr [6 x i16], ptr %73, i32 0, i32 2
  store i16 %72, ptr %74, align 4
  %75 = load ptr, ptr %5, align 4
  %76 = getelementptr i8, ptr %75, i32 128
  %77 = load ptr, ptr %76, align 4
  %78 = getelementptr i8, ptr %77, i32 240
  %79 = load i32, ptr %78, align 4
  %80 = add i32 %79, 15
  %81 = trunc i32 %80 to i16
  %82 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 8
  %83 = getelementptr [6 x i16], ptr %82, i32 0, i32 3
  store i16 %81, ptr %83, align 2
  %84 = load ptr, ptr %5, align 4
  %85 = getelementptr i8, ptr %84, i32 128
  %86 = load ptr, ptr %85, align 4
  %87 = getelementptr i8, ptr %86, i32 244
  %88 = load i32, ptr %87, align 4
  %89 = add i32 %88, 15
  %90 = trunc i32 %89 to i16
  %91 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 8
  %92 = getelementptr [6 x i16], ptr %91, i32 0, i32 5
  store i16 %90, ptr %92, align 2
  %93 = load ptr, ptr %5, align 4
  %94 = getelementptr i8, ptr %93, i32 128
  %95 = load ptr, ptr %94, align 4
  %96 = getelementptr i8, ptr %95, i32 136
  %97 = load i32, ptr %96, align 4
  %98 = sub i32 %97, 15
  %99 = trunc i32 %98 to i16
  %100 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 8
  %101 = getelementptr [6 x i16], ptr %100, i32 0, i32 0
  store i16 %99, ptr %101, align 4
  %102 = load ptr, ptr %5, align 4
  %103 = getelementptr i8, ptr %102, i32 128
  %104 = load ptr, ptr %103, align 4
  %105 = getelementptr i8, ptr %104, i32 140
  %106 = load i32, ptr %105, align 4
  %107 = sub i32 %106, 15
  %108 = trunc i32 %107 to i16
  %109 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 8
  %110 = getelementptr [6 x i16], ptr %109, i32 0, i32 1
  store i16 %108, ptr %110, align 2
  %111 = load ptr, ptr %5, align 4
  %112 = getelementptr i8, ptr %111, i32 128
  %113 = load ptr, ptr %112, align 4
  %114 = getelementptr i8, ptr %113, i32 144
  %115 = load i32, ptr %114, align 4
  %116 = sub i32 %115, 50
  %117 = trunc i32 %116 to i16
  %118 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 8
  %119 = getelementptr [6 x i16], ptr %118, i32 0, i32 4
  store i16 %117, ptr %119, align 4
  store i8 1, ptr %35, align 1
  %120 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 13
  %121 = getelementptr inbounds [8 x i8], ptr %120, i32 0, i32 0
  store ptr %121, ptr %36, align 4
  br label %122

122:                                              ; preds = %126, %2
  %123 = load i8, ptr %35, align 1
  %124 = load ptr, ptr %36, align 4
  %125 = getelementptr i8, ptr %124, i32 1
  store ptr %125, ptr %36, align 4
  store i8 %123, ptr %124, align 1
  br label %126

126:                                              ; preds = %122
  %127 = load ptr, ptr %36, align 4
  %128 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 13
  %129 = getelementptr inbounds [8 x i8], ptr %128, i32 0, i32 0
  %130 = getelementptr i8, ptr %129, i32 6
  %131 = icmp ult ptr %127, %130
  br i1 %131, label %122, label %132, !llvm.loop !5

132:                                              ; preds = %126
  %133 = load ptr, ptr %4, align 4
  %134 = getelementptr i8, ptr %133, i32 92
  %135 = load ptr, ptr %134, align 4
  %136 = getelementptr i8, ptr %135, i32 84
  %137 = load i32, ptr %136, align 4
  %138 = load ptr, ptr %28, align 4
  %139 = getelementptr i8, ptr %138, i32 20
  %140 = load i32, ptr %139, align 4
  %141 = sub i32 %137, %140
  %142 = trunc i32 %141 to i16
  %143 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 4
  %144 = getelementptr [3 x i16], ptr %143, i32 0, i32 0
  store i16 %142, ptr %144, align 4
  %145 = load ptr, ptr %4, align 4
  %146 = getelementptr i8, ptr %145, i32 92
  %147 = load ptr, ptr %146, align 4
  %148 = getelementptr i8, ptr %147, i32 88
  %149 = load i32, ptr %148, align 4
  %150 = load ptr, ptr %28, align 4
  %151 = getelementptr i8, ptr %150, i32 24
  %152 = load i32, ptr %151, align 4
  %153 = sub i32 %149, %152
  %154 = trunc i32 %153 to i16
  %155 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 4
  %156 = getelementptr [3 x i16], ptr %155, i32 0, i32 1
  store i16 %154, ptr %156, align 2
  %157 = load ptr, ptr %4, align 4
  %158 = getelementptr i8, ptr %157, i32 92
  %159 = load ptr, ptr %158, align 4
  %160 = getelementptr i8, ptr %159, i32 92
  %161 = load i32, ptr %160, align 4
  %162 = load ptr, ptr %28, align 4
  %163 = getelementptr i8, ptr %162, i32 28
  %164 = load i32, ptr %163, align 4
  %165 = sub i32 %161, %164
  %166 = trunc i32 %165 to i16
  %167 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 4
  %168 = getelementptr [3 x i16], ptr %167, i32 0, i32 2
  store i16 %166, ptr %168, align 4
  %169 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 7
  %170 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 4
  %171 = getelementptr inbounds [3 x i16], ptr %170, i32 0, i32 0
  %172 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  call void @func_8002E530(ptr noundef %169, ptr noundef %171, ptr noundef %172) #3
  %173 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %174 = getelementptr inbounds %struct.Vec3, ptr %173, i32 0, i32 0
  %175 = load i32, ptr %174, align 4
  %176 = icmp sgt i32 %175, 0
  br i1 %176, label %177, label %180

177:                                              ; preds = %132
  %178 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 13
  %179 = getelementptr [8 x i8], ptr %178, i32 0, i32 1
  store i8 0, ptr %179, align 1
  br label %200

180:                                              ; preds = %132
  %181 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %182 = getelementptr inbounds %struct.Vec3, ptr %181, i32 0, i32 0
  %183 = load i32, ptr %182, align 4
  %184 = icmp eq i32 %183, 0
  br i1 %184, label %185, label %190

185:                                              ; preds = %180
  %186 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 13
  %187 = getelementptr [8 x i8], ptr %186, i32 0, i32 1
  store i8 0, ptr %187, align 1
  %188 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 13
  %189 = getelementptr [8 x i8], ptr %188, i32 0, i32 3
  store i8 0, ptr %189, align 1
  br label %199

190:                                              ; preds = %180
  %191 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %192 = getelementptr inbounds %struct.Vec3, ptr %191, i32 0, i32 0
  %193 = load i32, ptr %192, align 4
  %194 = icmp slt i32 %193, 0
  br i1 %194, label %195, label %198

195:                                              ; preds = %190
  %196 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 13
  %197 = getelementptr [8 x i8], ptr %196, i32 0, i32 3
  store i8 0, ptr %197, align 1
  br label %198

198:                                              ; preds = %195, %190
  br label %199

199:                                              ; preds = %198, %185
  br label %200

200:                                              ; preds = %199, %177
  %201 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %202 = getelementptr inbounds %struct.Vec3, ptr %201, i32 0, i32 1
  %203 = load i32, ptr %202, align 4
  %204 = icmp sgt i32 %203, 0
  br i1 %204, label %205, label %208

205:                                              ; preds = %200
  %206 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 13
  %207 = getelementptr [8 x i8], ptr %206, i32 0, i32 4
  store i8 0, ptr %207, align 4
  br label %228

208:                                              ; preds = %200
  %209 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %210 = getelementptr inbounds %struct.Vec3, ptr %209, i32 0, i32 1
  %211 = load i32, ptr %210, align 4
  %212 = icmp eq i32 %211, 0
  br i1 %212, label %213, label %218

213:                                              ; preds = %208
  %214 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 13
  %215 = getelementptr [8 x i8], ptr %214, i32 0, i32 4
  store i8 0, ptr %215, align 4
  %216 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 13
  %217 = getelementptr [8 x i8], ptr %216, i32 0, i32 5
  store i8 0, ptr %217, align 1
  br label %227

218:                                              ; preds = %208
  %219 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %220 = getelementptr inbounds %struct.Vec3, ptr %219, i32 0, i32 1
  %221 = load i32, ptr %220, align 4
  %222 = icmp slt i32 %221, 0
  br i1 %222, label %223, label %226

223:                                              ; preds = %218
  %224 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 13
  %225 = getelementptr [8 x i8], ptr %224, i32 0, i32 5
  store i8 0, ptr %225, align 1
  br label %226

226:                                              ; preds = %223, %218
  br label %227

227:                                              ; preds = %226, %213
  br label %228

228:                                              ; preds = %227, %205
  %229 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %230 = getelementptr inbounds %struct.Vec3, ptr %229, i32 0, i32 2
  %231 = load i32, ptr %230, align 4
  %232 = icmp sgt i32 %231, 0
  br i1 %232, label %233, label %236

233:                                              ; preds = %228
  %234 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 13
  %235 = getelementptr [8 x i8], ptr %234, i32 0, i32 0
  store i8 0, ptr %235, align 4
  br label %256

236:                                              ; preds = %228
  %237 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %238 = getelementptr inbounds %struct.Vec3, ptr %237, i32 0, i32 2
  %239 = load i32, ptr %238, align 4
  %240 = icmp eq i32 %239, 0
  br i1 %240, label %241, label %246

241:                                              ; preds = %236
  %242 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 13
  %243 = getelementptr [8 x i8], ptr %242, i32 0, i32 0
  store i8 0, ptr %243, align 4
  %244 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 13
  %245 = getelementptr [8 x i8], ptr %244, i32 0, i32 2
  store i8 0, ptr %245, align 2
  br label %255

246:                                              ; preds = %236
  %247 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %248 = getelementptr inbounds %struct.Vec3, ptr %247, i32 0, i32 2
  %249 = load i32, ptr %248, align 4
  %250 = icmp slt i32 %249, 0
  br i1 %250, label %251, label %254

251:                                              ; preds = %246
  %252 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 13
  %253 = getelementptr [8 x i8], ptr %252, i32 0, i32 2
  store i8 0, ptr %253, align 2
  br label %254

254:                                              ; preds = %251, %246
  br label %255

255:                                              ; preds = %254, %241
  br label %256

256:                                              ; preds = %255, %233
  %257 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 11
  %258 = getelementptr inbounds %struct.anon, ptr %257, i32 0, i32 0
  store i16 0, ptr %258, align 4
  %259 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 11
  %260 = getelementptr inbounds %struct.anon, ptr %259, i32 0, i32 1
  store i16 0, ptr %260, align 2
  %261 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %262 = getelementptr inbounds %struct.Vec3, ptr %261, i32 0, i32 0
  %263 = load i32, ptr %262, align 4
  %264 = trunc i32 %263 to i16
  %265 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 12
  %266 = getelementptr inbounds %struct.anon, ptr %265, i32 0, i32 0
  store i16 %264, ptr %266, align 4
  %267 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %268 = getelementptr inbounds %struct.Vec3, ptr %267, i32 0, i32 2
  %269 = load i32, ptr %268, align 4
  %270 = trunc i32 %269 to i16
  %271 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 12
  %272 = getelementptr inbounds %struct.anon, ptr %271, i32 0, i32 1
  store i16 %270, ptr %272, align 2
  store i32 0, ptr %19, align 4
  %273 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 14
  store ptr %273, ptr %37, align 4
  store ptr @D_8009E270, ptr %32, align 4
  br label %274

274:                                              ; preds = %370, %256
  %275 = load i32, ptr %19, align 4
  %276 = icmp ult i32 %275, 4
  br i1 %276, label %277, label %375

277:                                              ; preds = %274
  %278 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 13
  %279 = load i32, ptr %19, align 4
  %280 = getelementptr [8 x i8], ptr %278, i32 0, i32 %279
  %281 = load i8, ptr %280, align 1
  %282 = icmp ne i8 %281, 0
  br i1 %282, label %283, label %369

283:                                              ; preds = %277
  %284 = load ptr, ptr %5, align 4
  %285 = getelementptr i8, ptr %284, i32 128
  %286 = load ptr, ptr %285, align 4
  %287 = load ptr, ptr %32, align 4
  %288 = getelementptr i8, ptr %287, i32 0
  %289 = load i8, ptr %288, align 1
  %290 = sext i8 %289 to i32
  %291 = mul i32 %290, 20
  %292 = add i32 %291, 96
  %293 = getelementptr i8, ptr %286, i32 %292
  %294 = load i32, ptr %293, align 4
  %295 = trunc i32 %294 to i16
  %296 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 10
  %297 = getelementptr inbounds %struct.anon, ptr %296, i32 0, i32 0
  store i16 %295, ptr %297, align 4
  %298 = load ptr, ptr %5, align 4
  %299 = getelementptr i8, ptr %298, i32 128
  %300 = load ptr, ptr %299, align 4
  %301 = load ptr, ptr %32, align 4
  %302 = getelementptr i8, ptr %301, i32 0
  %303 = load i8, ptr %302, align 1
  %304 = sext i8 %303 to i32
  %305 = mul i32 %304, 20
  %306 = add i32 %305, 104
  %307 = getelementptr i8, ptr %300, i32 %306
  %308 = load i32, ptr %307, align 4
  %309 = trunc i32 %308 to i16
  %310 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 10
  %311 = getelementptr inbounds %struct.anon, ptr %310, i32 0, i32 1
  store i16 %309, ptr %311, align 2
  %312 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 10
  %313 = load i32, ptr %312, align 4
  %314 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 11
  %315 = load i32, ptr %314, align 4
  %316 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 12
  %317 = load i32, ptr %316, align 4
  %318 = load ptr, ptr %37, align 4
  call void @func_8002D75C(i32 noundef %313, i32 noundef %315, i32 noundef %317, ptr noundef %318) #3
  %319 = load i32, ptr %19, align 4
  %320 = mul i32 %319, 2
  store i32 %320, ptr %22, align 4
  %321 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 14
  %322 = load i32, ptr %321, align 4
  %323 = icmp sgt i32 %322, 0
  br i1 %323, label %324, label %368

324:                                              ; preds = %283
  %325 = load ptr, ptr %5, align 4
  %326 = getelementptr i8, ptr %325, i32 128
  %327 = load ptr, ptr %326, align 4
  %328 = load i32, ptr %22, align 4
  %329 = getelementptr [0 x i8], ptr @D_8009E271, i32 0, i32 %328
  %330 = load i8, ptr %329, align 1
  %331 = sext i8 %330 to i32
  %332 = mul i32 %331, 20
  %333 = add i32 %332, 96
  %334 = getelementptr i8, ptr %327, i32 %333
  %335 = load i32, ptr %334, align 4
  %336 = trunc i32 %335 to i16
  %337 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 10
  %338 = getelementptr inbounds %struct.anon, ptr %337, i32 0, i32 0
  store i16 %336, ptr %338, align 4
  %339 = load ptr, ptr %5, align 4
  %340 = getelementptr i8, ptr %339, i32 128
  %341 = load ptr, ptr %340, align 4
  %342 = load i32, ptr %22, align 4
  %343 = getelementptr [0 x i8], ptr @D_8009E271, i32 0, i32 %342
  %344 = load i8, ptr %343, align 1
  %345 = sext i8 %344 to i32
  %346 = mul i32 %345, 20
  %347 = add i32 %346, 104
  %348 = getelementptr i8, ptr %341, i32 %347
  %349 = load i32, ptr %348, align 4
  %350 = trunc i32 %349 to i16
  %351 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 10
  %352 = getelementptr inbounds %struct.anon, ptr %351, i32 0, i32 1
  store i16 %350, ptr %352, align 2
  %353 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 10
  %354 = load i32, ptr %353, align 4
  %355 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 11
  %356 = load i32, ptr %355, align 4
  %357 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 12
  %358 = load i32, ptr %357, align 4
  %359 = load ptr, ptr %37, align 4
  call void @func_8002D75C(i32 noundef %354, i32 noundef %356, i32 noundef %358, ptr noundef %359) #3
  %360 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 14
  %361 = load i32, ptr %360, align 4
  %362 = icmp slt i32 %361, 0
  br i1 %362, label %363, label %364

363:                                              ; preds = %324
  br label %375

364:                                              ; preds = %324
  %365 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 13
  %366 = load i32, ptr %19, align 4
  %367 = getelementptr [8 x i8], ptr %365, i32 0, i32 %366
  store i8 0, ptr %367, align 1
  br label %368

368:                                              ; preds = %364, %283
  br label %369

369:                                              ; preds = %368, %277
  br label %370

370:                                              ; preds = %369
  %371 = load i32, ptr %19, align 4
  %372 = add i32 %371, 1
  store i32 %372, ptr %19, align 4
  %373 = load ptr, ptr %32, align 4
  %374 = getelementptr i8, ptr %373, i32 2
  store ptr %374, ptr %32, align 4
  br label %274, !llvm.loop !7

375:                                              ; preds = %363, %274
  %376 = load i32, ptr %19, align 4
  %377 = add i32 %376, 1
  store i32 %377, ptr %22, align 4
  br label %378

378:                                              ; preds = %385, %375
  %379 = load i32, ptr %22, align 4
  %380 = icmp ult i32 %379, 4
  br i1 %380, label %381, label %388

381:                                              ; preds = %378
  %382 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 13
  %383 = load i32, ptr %22, align 4
  %384 = getelementptr [8 x i8], ptr %382, i32 0, i32 %383
  store i8 0, ptr %384, align 1
  br label %385

385:                                              ; preds = %381
  %386 = load i32, ptr %22, align 4
  %387 = add i32 %386, 1
  store i32 %387, ptr %22, align 4
  br label %378, !llvm.loop !8

388:                                              ; preds = %378
  %389 = load i32, ptr %19, align 4
  %390 = icmp eq i32 %389, 2
  br i1 %390, label %394, label %391

391:                                              ; preds = %388
  %392 = load i32, ptr %19, align 4
  %393 = icmp eq i32 %392, 0
  br i1 %393, label %394, label %518

394:                                              ; preds = %391, %388
  %395 = load i32, ptr %19, align 4
  store i32 %395, ptr %23, align 4
  %396 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %397 = getelementptr inbounds %struct.Vec3, ptr %396, i32 0, i32 1
  %398 = load i32, ptr %397, align 4
  %399 = trunc i32 %398 to i16
  %400 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 12
  %401 = getelementptr inbounds %struct.anon, ptr %400, i32 0, i32 0
  store i16 %399, ptr %401, align 4
  %402 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %403 = getelementptr inbounds %struct.Vec3, ptr %402, i32 0, i32 2
  %404 = load i32, ptr %403, align 4
  %405 = trunc i32 %404 to i16
  %406 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 12
  %407 = getelementptr inbounds %struct.anon, ptr %406, i32 0, i32 1
  store i16 %405, ptr %407, align 2
  %408 = load i32, ptr %23, align 4
  %409 = icmp ult i32 %408, 6
  br i1 %409, label %410, label %517

410:                                              ; preds = %394
  %411 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 14
  store ptr %411, ptr %38, align 4
  %412 = load i32, ptr %19, align 4
  %413 = mul i32 %412, 2
  %414 = getelementptr i8, ptr @D_8009E278, i32 %413
  store ptr %414, ptr %33, align 4
  br label %415

415:                                              ; preds = %511, %410
  %416 = load i32, ptr %23, align 4
  %417 = icmp ult i32 %416, 6
  br i1 %417, label %418, label %516

418:                                              ; preds = %415
  %419 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 13
  %420 = load i32, ptr %23, align 4
  %421 = getelementptr [8 x i8], ptr %419, i32 0, i32 %420
  %422 = load i8, ptr %421, align 1
  %423 = icmp ne i8 %422, 0
  br i1 %423, label %424, label %510

424:                                              ; preds = %418
  %425 = load ptr, ptr %5, align 4
  %426 = getelementptr i8, ptr %425, i32 128
  %427 = load ptr, ptr %426, align 4
  %428 = load ptr, ptr %33, align 4
  %429 = getelementptr i8, ptr %428, i32 0
  %430 = load i8, ptr %429, align 1
  %431 = sext i8 %430 to i32
  %432 = mul i32 %431, 20
  %433 = add i32 %432, 100
  %434 = getelementptr i8, ptr %427, i32 %433
  %435 = load i32, ptr %434, align 4
  %436 = trunc i32 %435 to i16
  %437 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 10
  %438 = getelementptr inbounds %struct.anon, ptr %437, i32 0, i32 0
  store i16 %436, ptr %438, align 4
  %439 = load ptr, ptr %5, align 4
  %440 = getelementptr i8, ptr %439, i32 128
  %441 = load ptr, ptr %440, align 4
  %442 = load ptr, ptr %33, align 4
  %443 = getelementptr i8, ptr %442, i32 0
  %444 = load i8, ptr %443, align 1
  %445 = sext i8 %444 to i32
  %446 = mul i32 %445, 20
  %447 = add i32 %446, 104
  %448 = getelementptr i8, ptr %441, i32 %447
  %449 = load i32, ptr %448, align 4
  %450 = trunc i32 %449 to i16
  %451 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 10
  %452 = getelementptr inbounds %struct.anon, ptr %451, i32 0, i32 1
  store i16 %450, ptr %452, align 2
  %453 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 10
  %454 = load i32, ptr %453, align 4
  %455 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 11
  %456 = load i32, ptr %455, align 4
  %457 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 12
  %458 = load i32, ptr %457, align 4
  %459 = load ptr, ptr %38, align 4
  call void @func_8002D75C(i32 noundef %454, i32 noundef %456, i32 noundef %458, ptr noundef %459) #3
  %460 = load i32, ptr %23, align 4
  %461 = mul i32 %460, 2
  store i32 %461, ptr %19, align 4
  %462 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 14
  %463 = load i32, ptr %462, align 4
  %464 = icmp sgt i32 %463, 0
  br i1 %464, label %465, label %509

465:                                              ; preds = %424
  %466 = load ptr, ptr %5, align 4
  %467 = getelementptr i8, ptr %466, i32 128
  %468 = load ptr, ptr %467, align 4
  %469 = load i32, ptr %19, align 4
  %470 = getelementptr [0 x i8], ptr @D_8009E279, i32 0, i32 %469
  %471 = load i8, ptr %470, align 1
  %472 = sext i8 %471 to i32
  %473 = mul i32 %472, 20
  %474 = add i32 %473, 100
  %475 = getelementptr i8, ptr %468, i32 %474
  %476 = load i32, ptr %475, align 4
  %477 = trunc i32 %476 to i16
  %478 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 10
  %479 = getelementptr inbounds %struct.anon, ptr %478, i32 0, i32 0
  store i16 %477, ptr %479, align 4
  %480 = load ptr, ptr %5, align 4
  %481 = getelementptr i8, ptr %480, i32 128
  %482 = load ptr, ptr %481, align 4
  %483 = load i32, ptr %19, align 4
  %484 = getelementptr [0 x i8], ptr @D_8009E279, i32 0, i32 %483
  %485 = load i8, ptr %484, align 1
  %486 = sext i8 %485 to i32
  %487 = mul i32 %486, 20
  %488 = add i32 %487, 104
  %489 = getelementptr i8, ptr %482, i32 %488
  %490 = load i32, ptr %489, align 4
  %491 = trunc i32 %490 to i16
  %492 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 10
  %493 = getelementptr inbounds %struct.anon, ptr %492, i32 0, i32 1
  store i16 %491, ptr %493, align 2
  %494 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 10
  %495 = load i32, ptr %494, align 4
  %496 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 11
  %497 = load i32, ptr %496, align 4
  %498 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 12
  %499 = load i32, ptr %498, align 4
  %500 = load ptr, ptr %38, align 4
  call void @func_8002D75C(i32 noundef %495, i32 noundef %497, i32 noundef %499, ptr noundef %500) #3
  %501 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 14
  %502 = load i32, ptr %501, align 4
  %503 = icmp slt i32 %502, 0
  br i1 %503, label %504, label %505

504:                                              ; preds = %465
  br label %516

505:                                              ; preds = %465
  %506 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 13
  %507 = load i32, ptr %23, align 4
  %508 = getelementptr [8 x i8], ptr %506, i32 0, i32 %507
  store i8 0, ptr %508, align 1
  br label %509

509:                                              ; preds = %505, %424
  br label %510

510:                                              ; preds = %509, %418
  br label %511

511:                                              ; preds = %510
  %512 = load i32, ptr %23, align 4
  %513 = add i32 %512, 1
  store i32 %513, ptr %23, align 4
  %514 = load ptr, ptr %33, align 4
  %515 = getelementptr i8, ptr %514, i32 2
  store ptr %515, ptr %33, align 4
  br label %415, !llvm.loop !9

516:                                              ; preds = %504, %415
  br label %517

517:                                              ; preds = %516, %394
  br label %642

518:                                              ; preds = %391
  %519 = load i32, ptr %19, align 4
  store i32 %519, ptr %23, align 4
  %520 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %521 = getelementptr inbounds %struct.Vec3, ptr %520, i32 0, i32 0
  %522 = load i32, ptr %521, align 4
  %523 = trunc i32 %522 to i16
  %524 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 12
  %525 = getelementptr inbounds %struct.anon, ptr %524, i32 0, i32 0
  store i16 %523, ptr %525, align 4
  %526 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %527 = getelementptr inbounds %struct.Vec3, ptr %526, i32 0, i32 1
  %528 = load i32, ptr %527, align 4
  %529 = trunc i32 %528 to i16
  %530 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 12
  %531 = getelementptr inbounds %struct.anon, ptr %530, i32 0, i32 1
  store i16 %529, ptr %531, align 2
  %532 = load i32, ptr %23, align 4
  %533 = icmp ult i32 %532, 6
  br i1 %533, label %534, label %641

534:                                              ; preds = %518
  %535 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 14
  store ptr %535, ptr %39, align 4
  %536 = load i32, ptr %19, align 4
  %537 = mul i32 %536, 2
  %538 = getelementptr i8, ptr @D_8009E284, i32 %537
  store ptr %538, ptr %34, align 4
  br label %539

539:                                              ; preds = %635, %534
  %540 = load i32, ptr %23, align 4
  %541 = icmp ult i32 %540, 6
  br i1 %541, label %542, label %640

542:                                              ; preds = %539
  %543 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 13
  %544 = load i32, ptr %23, align 4
  %545 = getelementptr [8 x i8], ptr %543, i32 0, i32 %544
  %546 = load i8, ptr %545, align 1
  %547 = icmp ne i8 %546, 0
  br i1 %547, label %548, label %634

548:                                              ; preds = %542
  %549 = load ptr, ptr %5, align 4
  %550 = getelementptr i8, ptr %549, i32 128
  %551 = load ptr, ptr %550, align 4
  %552 = load ptr, ptr %34, align 4
  %553 = getelementptr i8, ptr %552, i32 0
  %554 = load i8, ptr %553, align 1
  %555 = sext i8 %554 to i32
  %556 = mul i32 %555, 20
  %557 = add i32 %556, 96
  %558 = getelementptr i8, ptr %551, i32 %557
  %559 = load i32, ptr %558, align 4
  %560 = trunc i32 %559 to i16
  %561 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 10
  %562 = getelementptr inbounds %struct.anon, ptr %561, i32 0, i32 0
  store i16 %560, ptr %562, align 4
  %563 = load ptr, ptr %5, align 4
  %564 = getelementptr i8, ptr %563, i32 128
  %565 = load ptr, ptr %564, align 4
  %566 = load ptr, ptr %34, align 4
  %567 = getelementptr i8, ptr %566, i32 0
  %568 = load i8, ptr %567, align 1
  %569 = sext i8 %568 to i32
  %570 = mul i32 %569, 20
  %571 = add i32 %570, 100
  %572 = getelementptr i8, ptr %565, i32 %571
  %573 = load i32, ptr %572, align 4
  %574 = trunc i32 %573 to i16
  %575 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 10
  %576 = getelementptr inbounds %struct.anon, ptr %575, i32 0, i32 1
  store i16 %574, ptr %576, align 2
  %577 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 10
  %578 = load i32, ptr %577, align 4
  %579 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 11
  %580 = load i32, ptr %579, align 4
  %581 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 12
  %582 = load i32, ptr %581, align 4
  %583 = load ptr, ptr %39, align 4
  call void @func_8002D75C(i32 noundef %578, i32 noundef %580, i32 noundef %582, ptr noundef %583) #3
  %584 = load i32, ptr %23, align 4
  %585 = mul i32 %584, 2
  store i32 %585, ptr %19, align 4
  %586 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 14
  %587 = load i32, ptr %586, align 4
  %588 = icmp slt i32 %587, 0
  br i1 %588, label %589, label %633

589:                                              ; preds = %548
  %590 = load ptr, ptr %5, align 4
  %591 = getelementptr i8, ptr %590, i32 128
  %592 = load ptr, ptr %591, align 4
  %593 = load i32, ptr %19, align 4
  %594 = getelementptr [0 x i8], ptr @D_8009E285, i32 0, i32 %593
  %595 = load i8, ptr %594, align 1
  %596 = sext i8 %595 to i32
  %597 = mul i32 %596, 20
  %598 = add i32 %597, 96
  %599 = getelementptr i8, ptr %592, i32 %598
  %600 = load i32, ptr %599, align 4
  %601 = trunc i32 %600 to i16
  %602 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 10
  %603 = getelementptr inbounds %struct.anon, ptr %602, i32 0, i32 0
  store i16 %601, ptr %603, align 4
  %604 = load ptr, ptr %5, align 4
  %605 = getelementptr i8, ptr %604, i32 128
  %606 = load ptr, ptr %605, align 4
  %607 = load i32, ptr %19, align 4
  %608 = getelementptr [0 x i8], ptr @D_8009E285, i32 0, i32 %607
  %609 = load i8, ptr %608, align 1
  %610 = sext i8 %609 to i32
  %611 = mul i32 %610, 20
  %612 = add i32 %611, 100
  %613 = getelementptr i8, ptr %606, i32 %612
  %614 = load i32, ptr %613, align 4
  %615 = trunc i32 %614 to i16
  %616 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 10
  %617 = getelementptr inbounds %struct.anon, ptr %616, i32 0, i32 1
  store i16 %615, ptr %617, align 2
  %618 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 10
  %619 = load i32, ptr %618, align 4
  %620 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 11
  %621 = load i32, ptr %620, align 4
  %622 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 12
  %623 = load i32, ptr %622, align 4
  %624 = load ptr, ptr %39, align 4
  call void @func_8002D75C(i32 noundef %619, i32 noundef %621, i32 noundef %623, ptr noundef %624) #3
  %625 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 14
  %626 = load i32, ptr %625, align 4
  %627 = icmp sgt i32 %626, 0
  br i1 %627, label %628, label %629

628:                                              ; preds = %589
  br label %640

629:                                              ; preds = %589
  %630 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 13
  %631 = load i32, ptr %23, align 4
  %632 = getelementptr [8 x i8], ptr %630, i32 0, i32 %631
  store i8 0, ptr %632, align 1
  br label %633

633:                                              ; preds = %629, %548
  br label %634

634:                                              ; preds = %633, %542
  br label %635

635:                                              ; preds = %634
  %636 = load i32, ptr %23, align 4
  %637 = add i32 %636, 1
  store i32 %637, ptr %23, align 4
  %638 = load ptr, ptr %34, align 4
  %639 = getelementptr i8, ptr %638, i32 2
  store ptr %639, ptr %34, align 4
  br label %539, !llvm.loop !10

640:                                              ; preds = %628, %539
  br label %641

641:                                              ; preds = %640, %518
  br label %642

642:                                              ; preds = %641, %517
  %643 = load i32, ptr %23, align 4
  store i32 %643, ptr %18, align 4
  store i32 0, ptr %17, align 4
  store i32 0, ptr %19, align 4
  %644 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  store ptr %644, ptr %7, align 4
  %645 = load ptr, ptr %7, align 4
  %646 = icmp ne ptr %645, null
  br i1 %646, label %647, label %654

647:                                              ; preds = %642
  %648 = load i32, ptr %10, align 4
  %649 = load i32, ptr %18, align 4
  %650 = trunc i32 %649 to i8
  %651 = zext i8 %650 to i32
  %652 = lshr i32 %651, 8
  %653 = add i32 %648, %652
  store i32 %653, ptr %22, align 4
  br label %656

654:                                              ; preds = %642
  %655 = load i32, ptr %10, align 4
  store i32 %655, ptr %22, align 4
  br label %656

656:                                              ; preds = %654, %647
  %657 = load i32, ptr %22, align 4
  %658 = shl i32 %657, 3
  %659 = load i32, ptr %22, align 4
  %660 = sub i32 %658, %659
  store i32 %660, ptr %24, align 4
  %661 = load i32, ptr %24, align 4
  %662 = shl i32 %661, 3
  store i32 %662, ptr %24, align 4
  %663 = load ptr, ptr %8, align 4
  %664 = load i32, ptr %24, align 4
  %665 = getelementptr i8, ptr %663, i32 %664
  store ptr %665, ptr %30, align 4
  br label %666

666:                                              ; preds = %1432, %656
  %667 = load i32, ptr %19, align 4
  %668 = icmp ult i32 %667, 12
  %669 = zext i1 %668 to i32
  store i32 %669, ptr %16, align 4
  br i1 %668, label %670, label %1435

670:                                              ; preds = %666
  %671 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  %672 = load ptr, ptr %4, align 4
  %673 = getelementptr i8, ptr %672, i32 128
  %674 = load ptr, ptr %673, align 4
  %675 = getelementptr i8, ptr %674, i32 496
  %676 = load i32, ptr %19, align 4
  %677 = mul i32 %676, 20
  %678 = getelementptr i8, ptr %675, i32 %677
  call void @llvm.memcpy.p0.p0.i32(ptr align 4 %671, ptr align 4 %678, i32 12, i1 false)
  %679 = load ptr, ptr %28, align 4
  %680 = getelementptr i8, ptr %679, i32 20
  %681 = load i32, ptr %680, align 4
  %682 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  %683 = getelementptr inbounds %struct.Vec3, ptr %682, i32 0, i32 0
  %684 = load i32, ptr %683, align 4
  %685 = sub i32 %684, %681
  store i32 %685, ptr %683, align 4
  %686 = load ptr, ptr %28, align 4
  %687 = getelementptr i8, ptr %686, i32 24
  %688 = load i32, ptr %687, align 4
  %689 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  %690 = getelementptr inbounds %struct.Vec3, ptr %689, i32 0, i32 1
  %691 = load i32, ptr %690, align 4
  %692 = sub i32 %691, %688
  store i32 %692, ptr %690, align 4
  %693 = load ptr, ptr %28, align 4
  %694 = getelementptr i8, ptr %693, i32 28
  %695 = load i32, ptr %694, align 4
  %696 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  %697 = getelementptr inbounds %struct.Vec3, ptr %696, i32 0, i32 2
  %698 = load i32, ptr %697, align 4
  %699 = sub i32 %698, %695
  store i32 %699, ptr %697, align 4
  %700 = load ptr, ptr %7, align 4
  %701 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  call void @func_8002E778(ptr noundef %700, ptr noundef %701) #3
  %702 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %703 = getelementptr inbounds %struct.Vec3, ptr %702, i32 0, i32 1
  %704 = load i32, ptr %703, align 4
  %705 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %706 = getelementptr inbounds %struct.Vec3, ptr %705, i32 0, i32 2
  %707 = load i32, ptr %706, align 4
  %708 = add i32 %704, %707
  %709 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %710 = getelementptr inbounds %struct.Vec3, ptr %709, i32 0, i32 0
  %711 = load i32, ptr %710, align 4
  %712 = add i32 %711, %708
  store i32 %712, ptr %710, align 4
  %713 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %714 = getelementptr inbounds %struct.Vec3, ptr %713, i32 0, i32 0
  %715 = load i32, ptr %714, align 4
  %716 = icmp sgt i32 %715, 159999
  br i1 %716, label %717, label %718

717:                                              ; preds = %670
  br label %1432

718:                                              ; preds = %670
  %719 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  %720 = getelementptr inbounds %struct.Vec3, ptr %719, i32 0, i32 0
  %721 = load i32, ptr %720, align 4
  %722 = trunc i32 %721 to i16
  %723 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 4
  %724 = getelementptr [3 x i16], ptr %723, i32 0, i32 0
  store i16 %722, ptr %724, align 4
  %725 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  %726 = getelementptr inbounds %struct.Vec3, ptr %725, i32 0, i32 1
  %727 = load i32, ptr %726, align 4
  %728 = trunc i32 %727 to i16
  %729 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 4
  %730 = getelementptr [3 x i16], ptr %729, i32 0, i32 1
  store i16 %728, ptr %730, align 2
  %731 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  %732 = getelementptr inbounds %struct.Vec3, ptr %731, i32 0, i32 2
  %733 = load i32, ptr %732, align 4
  %734 = trunc i32 %733 to i16
  %735 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 4
  %736 = getelementptr [3 x i16], ptr %735, i32 0, i32 2
  store i16 %734, ptr %736, align 4
  %737 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 7
  %738 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 4
  %739 = getelementptr inbounds [3 x i16], ptr %738, i32 0, i32 0
  %740 = load ptr, ptr %7, align 4
  call void @func_8002E530(ptr noundef %737, ptr noundef %739, ptr noundef %740) #3
  %741 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  %742 = getelementptr inbounds %struct.Vec3, ptr %741, i32 0, i32 0
  %743 = load i32, ptr %742, align 4
  %744 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 8
  %745 = getelementptr [6 x i16], ptr %744, i32 0, i32 2
  %746 = load i16, ptr %745, align 4
  %747 = sext i16 %746 to i32
  %748 = icmp slt i32 %743, %747
  br i1 %748, label %749, label %794

749:                                              ; preds = %718
  %750 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 8
  %751 = getelementptr [6 x i16], ptr %750, i32 0, i32 0
  %752 = load i16, ptr %751, align 4
  %753 = sext i16 %752 to i32
  %754 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  %755 = getelementptr inbounds %struct.Vec3, ptr %754, i32 0, i32 0
  %756 = load i32, ptr %755, align 4
  %757 = icmp slt i32 %753, %756
  br i1 %757, label %758, label %794

758:                                              ; preds = %749
  %759 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  %760 = getelementptr inbounds %struct.Vec3, ptr %759, i32 0, i32 1
  %761 = load i32, ptr %760, align 4
  %762 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 8
  %763 = getelementptr [6 x i16], ptr %762, i32 0, i32 3
  %764 = load i16, ptr %763, align 2
  %765 = sext i16 %764 to i32
  %766 = icmp slt i32 %761, %765
  br i1 %766, label %767, label %794

767:                                              ; preds = %758
  %768 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 8
  %769 = getelementptr [6 x i16], ptr %768, i32 0, i32 1
  %770 = load i16, ptr %769, align 2
  %771 = sext i16 %770 to i32
  %772 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  %773 = getelementptr inbounds %struct.Vec3, ptr %772, i32 0, i32 1
  %774 = load i32, ptr %773, align 4
  %775 = icmp slt i32 %771, %774
  br i1 %775, label %776, label %794

776:                                              ; preds = %767
  %777 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  %778 = getelementptr inbounds %struct.Vec3, ptr %777, i32 0, i32 2
  %779 = load i32, ptr %778, align 4
  %780 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 8
  %781 = getelementptr [6 x i16], ptr %780, i32 0, i32 5
  %782 = load i16, ptr %781, align 2
  %783 = sext i16 %782 to i32
  %784 = icmp slt i32 %779, %783
  br i1 %784, label %785, label %794

785:                                              ; preds = %776
  %786 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 8
  %787 = getelementptr [6 x i16], ptr %786, i32 0, i32 4
  %788 = load i16, ptr %787, align 4
  %789 = sext i16 %788 to i32
  %790 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  %791 = getelementptr inbounds %struct.Vec3, ptr %790, i32 0, i32 2
  %792 = load i32, ptr %791, align 4
  %793 = icmp slt i32 %789, %792
  br i1 %793, label %795, label %794

794:                                              ; preds = %785, %776, %767, %758, %749, %718
  br label %1432

795:                                              ; preds = %785
  %796 = load i32, ptr %18, align 4
  switch i32 %796, label %1242 [
    i32 0, label %797
    i32 2, label %871
    i32 1, label %945
    i32 3, label %1028
    i32 4, label %1093
    i32 5, label %1168
  ]

797:                                              ; preds = %795
  %798 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 8
  %799 = getelementptr [6 x i16], ptr %798, i32 0, i32 4
  %800 = load i16, ptr %799, align 4
  %801 = sext i16 %800 to i32
  %802 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  %803 = getelementptr inbounds %struct.Vec3, ptr %802, i32 0, i32 2
  %804 = load i32, ptr %803, align 4
  %805 = sub i32 %801, %804
  %806 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %807 = getelementptr inbounds %struct.Vec3, ptr %806, i32 0, i32 0
  store i32 %805, ptr %807, align 4
  %808 = load ptr, ptr %28, align 4
  %809 = getelementptr i8, ptr %808, i32 0
  %810 = load i16, ptr %809, align 2
  %811 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %812 = getelementptr inbounds %struct.Matrix32, ptr %811, i32 0, i32 0
  %813 = getelementptr [9 x i16], ptr %812, i32 0, i32 0
  store i16 %810, ptr %813, align 4
  %814 = load ptr, ptr %28, align 4
  %815 = getelementptr i8, ptr %814, i32 6
  %816 = load i16, ptr %815, align 2
  %817 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %818 = getelementptr inbounds %struct.Matrix32, ptr %817, i32 0, i32 0
  %819 = getelementptr [9 x i16], ptr %818, i32 0, i32 3
  store i16 %816, ptr %819, align 2
  %820 = load ptr, ptr %28, align 4
  %821 = getelementptr i8, ptr %820, i32 12
  %822 = load i16, ptr %821, align 2
  %823 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %824 = getelementptr inbounds %struct.Matrix32, ptr %823, i32 0, i32 0
  %825 = getelementptr [9 x i16], ptr %824, i32 0, i32 6
  store i16 %822, ptr %825, align 4
  %826 = load ptr, ptr %28, align 4
  %827 = getelementptr i8, ptr %826, i32 4
  %828 = load i16, ptr %827, align 2
  %829 = zext i16 %828 to i32
  %830 = sub i32 0, %829
  %831 = trunc i32 %830 to i16
  %832 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %833 = getelementptr inbounds %struct.Matrix32, ptr %832, i32 0, i32 0
  %834 = getelementptr [9 x i16], ptr %833, i32 0, i32 1
  store i16 %831, ptr %834, align 2
  %835 = load ptr, ptr %28, align 4
  %836 = getelementptr i8, ptr %835, i32 10
  %837 = load i16, ptr %836, align 2
  %838 = zext i16 %837 to i32
  %839 = sub i32 0, %838
  %840 = trunc i32 %839 to i16
  %841 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %842 = getelementptr inbounds %struct.Matrix32, ptr %841, i32 0, i32 0
  %843 = getelementptr [9 x i16], ptr %842, i32 0, i32 4
  store i16 %840, ptr %843, align 4
  %844 = load ptr, ptr %28, align 4
  %845 = getelementptr i8, ptr %844, i32 16
  %846 = load i16, ptr %845, align 2
  %847 = zext i16 %846 to i32
  %848 = sub i32 0, %847
  %849 = trunc i32 %848 to i16
  %850 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %851 = getelementptr inbounds %struct.Matrix32, ptr %850, i32 0, i32 0
  %852 = getelementptr [9 x i16], ptr %851, i32 0, i32 7
  store i16 %849, ptr %852, align 2
  %853 = load ptr, ptr %28, align 4
  %854 = getelementptr i8, ptr %853, i32 2
  %855 = load i16, ptr %854, align 2
  %856 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %857 = getelementptr inbounds %struct.Matrix32, ptr %856, i32 0, i32 0
  %858 = getelementptr [9 x i16], ptr %857, i32 0, i32 2
  store i16 %855, ptr %858, align 4
  %859 = load ptr, ptr %28, align 4
  %860 = getelementptr i8, ptr %859, i32 8
  %861 = load i16, ptr %860, align 2
  %862 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %863 = getelementptr inbounds %struct.Matrix32, ptr %862, i32 0, i32 0
  %864 = getelementptr [9 x i16], ptr %863, i32 0, i32 5
  store i16 %861, ptr %864, align 2
  %865 = load ptr, ptr %28, align 4
  %866 = getelementptr i8, ptr %865, i32 14
  %867 = load i16, ptr %866, align 2
  %868 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %869 = getelementptr inbounds %struct.Matrix32, ptr %868, i32 0, i32 0
  %870 = getelementptr [9 x i16], ptr %869, i32 0, i32 8
  store i16 %867, ptr %870, align 4
  br label %1242

871:                                              ; preds = %795
  %872 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  %873 = getelementptr inbounds %struct.Vec3, ptr %872, i32 0, i32 2
  %874 = load i32, ptr %873, align 4
  %875 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 8
  %876 = getelementptr [6 x i16], ptr %875, i32 0, i32 5
  %877 = load i16, ptr %876, align 2
  %878 = sext i16 %877 to i32
  %879 = sub i32 %874, %878
  %880 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %881 = getelementptr inbounds %struct.Vec3, ptr %880, i32 0, i32 0
  store i32 %879, ptr %881, align 4
  %882 = load ptr, ptr %28, align 4
  %883 = getelementptr i8, ptr %882, i32 0
  %884 = load i16, ptr %883, align 2
  %885 = zext i16 %884 to i32
  %886 = sub i32 0, %885
  %887 = trunc i32 %886 to i16
  %888 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %889 = getelementptr inbounds %struct.Matrix32, ptr %888, i32 0, i32 0
  %890 = getelementptr [9 x i16], ptr %889, i32 0, i32 0
  store i16 %887, ptr %890, align 4
  %891 = load ptr, ptr %28, align 4
  %892 = getelementptr i8, ptr %891, i32 6
  %893 = load i16, ptr %892, align 2
  %894 = zext i16 %893 to i32
  %895 = sub i32 0, %894
  %896 = trunc i32 %895 to i16
  %897 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %898 = getelementptr inbounds %struct.Matrix32, ptr %897, i32 0, i32 0
  %899 = getelementptr [9 x i16], ptr %898, i32 0, i32 3
  store i16 %896, ptr %899, align 2
  %900 = load ptr, ptr %28, align 4
  %901 = getelementptr i8, ptr %900, i32 12
  %902 = load i16, ptr %901, align 2
  %903 = zext i16 %902 to i32
  %904 = sub i32 0, %903
  %905 = trunc i32 %904 to i16
  %906 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %907 = getelementptr inbounds %struct.Matrix32, ptr %906, i32 0, i32 0
  %908 = getelementptr [9 x i16], ptr %907, i32 0, i32 6
  store i16 %905, ptr %908, align 4
  %909 = load ptr, ptr %28, align 4
  %910 = getelementptr i8, ptr %909, i32 4
  %911 = load i16, ptr %910, align 2
  %912 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %913 = getelementptr inbounds %struct.Matrix32, ptr %912, i32 0, i32 0
  %914 = getelementptr [9 x i16], ptr %913, i32 0, i32 1
  store i16 %911, ptr %914, align 2
  %915 = load ptr, ptr %28, align 4
  %916 = getelementptr i8, ptr %915, i32 10
  %917 = load i16, ptr %916, align 2
  %918 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %919 = getelementptr inbounds %struct.Matrix32, ptr %918, i32 0, i32 0
  %920 = getelementptr [9 x i16], ptr %919, i32 0, i32 4
  store i16 %917, ptr %920, align 4
  %921 = load ptr, ptr %28, align 4
  %922 = getelementptr i8, ptr %921, i32 16
  %923 = load i16, ptr %922, align 2
  %924 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %925 = getelementptr inbounds %struct.Matrix32, ptr %924, i32 0, i32 0
  %926 = getelementptr [9 x i16], ptr %925, i32 0, i32 7
  store i16 %923, ptr %926, align 2
  %927 = load ptr, ptr %28, align 4
  %928 = getelementptr i8, ptr %927, i32 2
  %929 = load i16, ptr %928, align 2
  %930 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %931 = getelementptr inbounds %struct.Matrix32, ptr %930, i32 0, i32 0
  %932 = getelementptr [9 x i16], ptr %931, i32 0, i32 2
  store i16 %929, ptr %932, align 4
  %933 = load ptr, ptr %28, align 4
  %934 = getelementptr i8, ptr %933, i32 8
  %935 = load i16, ptr %934, align 2
  %936 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %937 = getelementptr inbounds %struct.Matrix32, ptr %936, i32 0, i32 0
  %938 = getelementptr [9 x i16], ptr %937, i32 0, i32 5
  store i16 %935, ptr %938, align 2
  %939 = load ptr, ptr %28, align 4
  %940 = getelementptr i8, ptr %939, i32 14
  %941 = load i16, ptr %940, align 2
  %942 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %943 = getelementptr inbounds %struct.Matrix32, ptr %942, i32 0, i32 0
  %944 = getelementptr [9 x i16], ptr %943, i32 0, i32 8
  store i16 %941, ptr %944, align 4
  br label %1242

945:                                              ; preds = %795
  %946 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 8
  %947 = getelementptr [6 x i16], ptr %946, i32 0, i32 0
  %948 = load i16, ptr %947, align 4
  %949 = sext i16 %948 to i32
  %950 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  %951 = getelementptr inbounds %struct.Vec3, ptr %950, i32 0, i32 0
  %952 = load i32, ptr %951, align 4
  %953 = sub i32 %949, %952
  %954 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %955 = getelementptr inbounds %struct.Vec3, ptr %954, i32 0, i32 0
  store i32 %953, ptr %955, align 4
  %956 = load ptr, ptr %28, align 4
  %957 = getelementptr i8, ptr %956, i32 4
  %958 = load i16, ptr %957, align 2
  %959 = zext i16 %958 to i32
  %960 = sub i32 0, %959
  %961 = trunc i32 %960 to i16
  %962 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %963 = getelementptr inbounds %struct.Matrix32, ptr %962, i32 0, i32 0
  %964 = getelementptr [9 x i16], ptr %963, i32 0, i32 0
  store i16 %961, ptr %964, align 4
  %965 = load ptr, ptr %28, align 4
  %966 = getelementptr i8, ptr %965, i32 10
  %967 = load i16, ptr %966, align 2
  %968 = zext i16 %967 to i32
  %969 = sub i32 0, %968
  %970 = trunc i32 %969 to i16
  %971 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %972 = getelementptr inbounds %struct.Matrix32, ptr %971, i32 0, i32 0
  %973 = getelementptr [9 x i16], ptr %972, i32 0, i32 3
  store i16 %970, ptr %973, align 2
  %974 = load ptr, ptr %28, align 4
  %975 = getelementptr i8, ptr %974, i32 16
  %976 = load i16, ptr %975, align 2
  %977 = zext i16 %976 to i32
  %978 = sub i32 0, %977
  %979 = trunc i32 %978 to i16
  %980 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %981 = getelementptr inbounds %struct.Matrix32, ptr %980, i32 0, i32 0
  %982 = getelementptr [9 x i16], ptr %981, i32 0, i32 6
  store i16 %979, ptr %982, align 4
  %983 = load ptr, ptr %28, align 4
  %984 = getelementptr i8, ptr %983, i32 0
  %985 = load i16, ptr %984, align 2
  %986 = zext i16 %985 to i32
  %987 = sub i32 0, %986
  %988 = trunc i32 %987 to i16
  %989 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %990 = getelementptr inbounds %struct.Matrix32, ptr %989, i32 0, i32 0
  %991 = getelementptr [9 x i16], ptr %990, i32 0, i32 1
  store i16 %988, ptr %991, align 2
  %992 = load ptr, ptr %28, align 4
  %993 = getelementptr i8, ptr %992, i32 6
  %994 = load i16, ptr %993, align 2
  %995 = zext i16 %994 to i32
  %996 = sub i32 0, %995
  %997 = trunc i32 %996 to i16
  %998 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %999 = getelementptr inbounds %struct.Matrix32, ptr %998, i32 0, i32 0
  %1000 = getelementptr [9 x i16], ptr %999, i32 0, i32 4
  store i16 %997, ptr %1000, align 4
  %1001 = load ptr, ptr %28, align 4
  %1002 = getelementptr i8, ptr %1001, i32 12
  %1003 = load i16, ptr %1002, align 2
  %1004 = zext i16 %1003 to i32
  %1005 = sub i32 0, %1004
  %1006 = trunc i32 %1005 to i16
  %1007 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1008 = getelementptr inbounds %struct.Matrix32, ptr %1007, i32 0, i32 0
  %1009 = getelementptr [9 x i16], ptr %1008, i32 0, i32 7
  store i16 %1006, ptr %1009, align 2
  %1010 = load ptr, ptr %28, align 4
  %1011 = getelementptr i8, ptr %1010, i32 2
  %1012 = load i16, ptr %1011, align 2
  %1013 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1014 = getelementptr inbounds %struct.Matrix32, ptr %1013, i32 0, i32 0
  %1015 = getelementptr [9 x i16], ptr %1014, i32 0, i32 2
  store i16 %1012, ptr %1015, align 4
  %1016 = load ptr, ptr %28, align 4
  %1017 = getelementptr i8, ptr %1016, i32 8
  %1018 = load i16, ptr %1017, align 2
  %1019 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1020 = getelementptr inbounds %struct.Matrix32, ptr %1019, i32 0, i32 0
  %1021 = getelementptr [9 x i16], ptr %1020, i32 0, i32 5
  store i16 %1018, ptr %1021, align 2
  %1022 = load ptr, ptr %28, align 4
  %1023 = getelementptr i8, ptr %1022, i32 14
  %1024 = load i16, ptr %1023, align 2
  %1025 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1026 = getelementptr inbounds %struct.Matrix32, ptr %1025, i32 0, i32 0
  %1027 = getelementptr [9 x i16], ptr %1026, i32 0, i32 8
  store i16 %1024, ptr %1027, align 4
  br label %1242

1028:                                             ; preds = %795
  %1029 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  %1030 = getelementptr inbounds %struct.Vec3, ptr %1029, i32 0, i32 0
  %1031 = load i32, ptr %1030, align 4
  %1032 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 8
  %1033 = getelementptr [6 x i16], ptr %1032, i32 0, i32 2
  %1034 = load i16, ptr %1033, align 4
  %1035 = sext i16 %1034 to i32
  %1036 = sub i32 %1031, %1035
  %1037 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %1038 = getelementptr inbounds %struct.Vec3, ptr %1037, i32 0, i32 0
  store i32 %1036, ptr %1038, align 4
  %1039 = load ptr, ptr %28, align 4
  %1040 = getelementptr i8, ptr %1039, i32 4
  %1041 = load i16, ptr %1040, align 2
  %1042 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1043 = getelementptr inbounds %struct.Matrix32, ptr %1042, i32 0, i32 0
  %1044 = getelementptr [9 x i16], ptr %1043, i32 0, i32 0
  store i16 %1041, ptr %1044, align 4
  %1045 = load ptr, ptr %28, align 4
  %1046 = getelementptr i8, ptr %1045, i32 10
  %1047 = load i16, ptr %1046, align 2
  %1048 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1049 = getelementptr inbounds %struct.Matrix32, ptr %1048, i32 0, i32 0
  %1050 = getelementptr [9 x i16], ptr %1049, i32 0, i32 3
  store i16 %1047, ptr %1050, align 2
  %1051 = load ptr, ptr %28, align 4
  %1052 = getelementptr i8, ptr %1051, i32 16
  %1053 = load i16, ptr %1052, align 2
  %1054 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1055 = getelementptr inbounds %struct.Matrix32, ptr %1054, i32 0, i32 0
  %1056 = getelementptr [9 x i16], ptr %1055, i32 0, i32 6
  store i16 %1053, ptr %1056, align 4
  %1057 = load ptr, ptr %28, align 4
  %1058 = getelementptr i8, ptr %1057, i32 0
  %1059 = load i16, ptr %1058, align 2
  %1060 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1061 = getelementptr inbounds %struct.Matrix32, ptr %1060, i32 0, i32 0
  %1062 = getelementptr [9 x i16], ptr %1061, i32 0, i32 1
  store i16 %1059, ptr %1062, align 2
  %1063 = load ptr, ptr %28, align 4
  %1064 = getelementptr i8, ptr %1063, i32 6
  %1065 = load i16, ptr %1064, align 2
  %1066 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1067 = getelementptr inbounds %struct.Matrix32, ptr %1066, i32 0, i32 0
  %1068 = getelementptr [9 x i16], ptr %1067, i32 0, i32 4
  store i16 %1065, ptr %1068, align 4
  %1069 = load ptr, ptr %28, align 4
  %1070 = getelementptr i8, ptr %1069, i32 12
  %1071 = load i16, ptr %1070, align 2
  %1072 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1073 = getelementptr inbounds %struct.Matrix32, ptr %1072, i32 0, i32 0
  %1074 = getelementptr [9 x i16], ptr %1073, i32 0, i32 7
  store i16 %1071, ptr %1074, align 2
  %1075 = load ptr, ptr %28, align 4
  %1076 = getelementptr i8, ptr %1075, i32 2
  %1077 = load i16, ptr %1076, align 2
  %1078 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1079 = getelementptr inbounds %struct.Matrix32, ptr %1078, i32 0, i32 0
  %1080 = getelementptr [9 x i16], ptr %1079, i32 0, i32 2
  store i16 %1077, ptr %1080, align 4
  %1081 = load ptr, ptr %28, align 4
  %1082 = getelementptr i8, ptr %1081, i32 8
  %1083 = load i16, ptr %1082, align 2
  %1084 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1085 = getelementptr inbounds %struct.Matrix32, ptr %1084, i32 0, i32 0
  %1086 = getelementptr [9 x i16], ptr %1085, i32 0, i32 5
  store i16 %1083, ptr %1086, align 2
  %1087 = load ptr, ptr %28, align 4
  %1088 = getelementptr i8, ptr %1087, i32 14
  %1089 = load i16, ptr %1088, align 2
  %1090 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1091 = getelementptr inbounds %struct.Matrix32, ptr %1090, i32 0, i32 0
  %1092 = getelementptr [9 x i16], ptr %1091, i32 0, i32 8
  store i16 %1089, ptr %1092, align 4
  br label %1242

1093:                                             ; preds = %795
  %1094 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 8
  %1095 = getelementptr [6 x i16], ptr %1094, i32 0, i32 1
  %1096 = load i16, ptr %1095, align 2
  %1097 = sext i16 %1096 to i32
  %1098 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  %1099 = getelementptr inbounds %struct.Vec3, ptr %1098, i32 0, i32 1
  %1100 = load i32, ptr %1099, align 4
  %1101 = sub i32 %1097, %1100
  %1102 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %1103 = getelementptr inbounds %struct.Vec3, ptr %1102, i32 0, i32 0
  store i32 %1101, ptr %1103, align 4
  %1104 = load ptr, ptr %28, align 4
  store ptr %1104, ptr %13, align 4
  %1105 = load ptr, ptr %13, align 4
  %1106 = getelementptr i8, ptr %1105, i32 4
  %1107 = load i16, ptr %1106, align 2
  %1108 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1109 = getelementptr inbounds %struct.Matrix32, ptr %1108, i32 0, i32 0
  %1110 = getelementptr [9 x i16], ptr %1109, i32 0, i32 0
  store i16 %1107, ptr %1110, align 4
  %1111 = load ptr, ptr %28, align 4
  %1112 = getelementptr i8, ptr %1111, i32 10
  %1113 = load i16, ptr %1112, align 2
  %1114 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1115 = getelementptr inbounds %struct.Matrix32, ptr %1114, i32 0, i32 0
  %1116 = getelementptr [9 x i16], ptr %1115, i32 0, i32 3
  store i16 %1113, ptr %1116, align 2
  %1117 = load ptr, ptr %28, align 4
  %1118 = getelementptr i8, ptr %1117, i32 16
  %1119 = load i16, ptr %1118, align 2
  %1120 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1121 = getelementptr inbounds %struct.Matrix32, ptr %1120, i32 0, i32 0
  %1122 = getelementptr [9 x i16], ptr %1121, i32 0, i32 6
  store i16 %1119, ptr %1122, align 4
  %1123 = load ptr, ptr %28, align 4
  %1124 = getelementptr i8, ptr %1123, i32 2
  %1125 = load i16, ptr %1124, align 2
  %1126 = zext i16 %1125 to i32
  %1127 = sub i32 0, %1126
  %1128 = trunc i32 %1127 to i16
  %1129 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1130 = getelementptr inbounds %struct.Matrix32, ptr %1129, i32 0, i32 0
  %1131 = getelementptr [9 x i16], ptr %1130, i32 0, i32 1
  store i16 %1128, ptr %1131, align 2
  %1132 = load ptr, ptr %28, align 4
  %1133 = getelementptr i8, ptr %1132, i32 8
  %1134 = load i16, ptr %1133, align 2
  %1135 = zext i16 %1134 to i32
  %1136 = sub i32 0, %1135
  %1137 = trunc i32 %1136 to i16
  %1138 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1139 = getelementptr inbounds %struct.Matrix32, ptr %1138, i32 0, i32 0
  %1140 = getelementptr [9 x i16], ptr %1139, i32 0, i32 4
  store i16 %1137, ptr %1140, align 4
  %1141 = load ptr, ptr %28, align 4
  %1142 = getelementptr i8, ptr %1141, i32 14
  %1143 = load i16, ptr %1142, align 2
  %1144 = zext i16 %1143 to i32
  %1145 = sub i32 0, %1144
  %1146 = trunc i32 %1145 to i16
  %1147 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1148 = getelementptr inbounds %struct.Matrix32, ptr %1147, i32 0, i32 0
  %1149 = getelementptr [9 x i16], ptr %1148, i32 0, i32 7
  store i16 %1146, ptr %1149, align 2
  %1150 = load ptr, ptr %28, align 4
  %1151 = getelementptr i8, ptr %1150, i32 0
  %1152 = load i16, ptr %1151, align 2
  %1153 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1154 = getelementptr inbounds %struct.Matrix32, ptr %1153, i32 0, i32 0
  %1155 = getelementptr [9 x i16], ptr %1154, i32 0, i32 2
  store i16 %1152, ptr %1155, align 4
  %1156 = load ptr, ptr %28, align 4
  %1157 = getelementptr i8, ptr %1156, i32 6
  %1158 = load i16, ptr %1157, align 2
  %1159 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1160 = getelementptr inbounds %struct.Matrix32, ptr %1159, i32 0, i32 0
  %1161 = getelementptr [9 x i16], ptr %1160, i32 0, i32 5
  store i16 %1158, ptr %1161, align 2
  %1162 = load ptr, ptr %28, align 4
  %1163 = getelementptr i8, ptr %1162, i32 12
  %1164 = load i16, ptr %1163, align 2
  %1165 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1166 = getelementptr inbounds %struct.Matrix32, ptr %1165, i32 0, i32 0
  %1167 = getelementptr [9 x i16], ptr %1166, i32 0, i32 8
  store i16 %1164, ptr %1167, align 4
  br label %1242

1168:                                             ; preds = %795
  %1169 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  %1170 = getelementptr inbounds %struct.Vec3, ptr %1169, i32 0, i32 1
  %1171 = load i32, ptr %1170, align 4
  %1172 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 8
  %1173 = getelementptr [6 x i16], ptr %1172, i32 0, i32 3
  %1174 = load i16, ptr %1173, align 2
  %1175 = sext i16 %1174 to i32
  %1176 = sub i32 %1171, %1175
  %1177 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %1178 = getelementptr inbounds %struct.Vec3, ptr %1177, i32 0, i32 0
  store i32 %1176, ptr %1178, align 4
  %1179 = load ptr, ptr %28, align 4
  %1180 = getelementptr i8, ptr %1179, i32 4
  %1181 = load i16, ptr %1180, align 2
  %1182 = zext i16 %1181 to i32
  %1183 = sub i32 0, %1182
  %1184 = trunc i32 %1183 to i16
  %1185 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1186 = getelementptr inbounds %struct.Matrix32, ptr %1185, i32 0, i32 0
  %1187 = getelementptr [9 x i16], ptr %1186, i32 0, i32 0
  store i16 %1184, ptr %1187, align 4
  %1188 = load ptr, ptr %28, align 4
  %1189 = getelementptr i8, ptr %1188, i32 10
  %1190 = load i16, ptr %1189, align 2
  %1191 = zext i16 %1190 to i32
  %1192 = sub i32 0, %1191
  %1193 = trunc i32 %1192 to i16
  %1194 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1195 = getelementptr inbounds %struct.Matrix32, ptr %1194, i32 0, i32 0
  %1196 = getelementptr [9 x i16], ptr %1195, i32 0, i32 3
  store i16 %1193, ptr %1196, align 2
  %1197 = load ptr, ptr %28, align 4
  %1198 = getelementptr i8, ptr %1197, i32 16
  %1199 = load i16, ptr %1198, align 2
  %1200 = zext i16 %1199 to i32
  %1201 = sub i32 0, %1200
  %1202 = trunc i32 %1201 to i16
  %1203 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1204 = getelementptr inbounds %struct.Matrix32, ptr %1203, i32 0, i32 0
  %1205 = getelementptr [9 x i16], ptr %1204, i32 0, i32 6
  store i16 %1202, ptr %1205, align 4
  %1206 = load ptr, ptr %28, align 4
  store ptr %1206, ptr %12, align 4
  %1207 = getelementptr i8, ptr %1206, i32 2
  %1208 = load i16, ptr %1207, align 2
  %1209 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1210 = getelementptr inbounds %struct.Matrix32, ptr %1209, i32 0, i32 0
  %1211 = getelementptr [9 x i16], ptr %1210, i32 0, i32 1
  store i16 %1208, ptr %1211, align 2
  %1212 = load ptr, ptr %28, align 4
  %1213 = getelementptr i8, ptr %1212, i32 8
  %1214 = load i16, ptr %1213, align 2
  %1215 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1216 = getelementptr inbounds %struct.Matrix32, ptr %1215, i32 0, i32 0
  %1217 = getelementptr [9 x i16], ptr %1216, i32 0, i32 4
  store i16 %1214, ptr %1217, align 4
  %1218 = load ptr, ptr %28, align 4
  %1219 = getelementptr i8, ptr %1218, i32 14
  %1220 = load i16, ptr %1219, align 2
  %1221 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1222 = getelementptr inbounds %struct.Matrix32, ptr %1221, i32 0, i32 0
  %1223 = getelementptr [9 x i16], ptr %1222, i32 0, i32 7
  store i16 %1220, ptr %1223, align 2
  %1224 = load ptr, ptr %28, align 4
  %1225 = getelementptr i8, ptr %1224, i32 0
  %1226 = load i16, ptr %1225, align 2
  %1227 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1228 = getelementptr inbounds %struct.Matrix32, ptr %1227, i32 0, i32 0
  %1229 = getelementptr [9 x i16], ptr %1228, i32 0, i32 2
  store i16 %1226, ptr %1229, align 4
  %1230 = load ptr, ptr %28, align 4
  %1231 = getelementptr i8, ptr %1230, i32 6
  %1232 = load i16, ptr %1231, align 2
  %1233 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1234 = getelementptr inbounds %struct.Matrix32, ptr %1233, i32 0, i32 0
  %1235 = getelementptr [9 x i16], ptr %1234, i32 0, i32 5
  store i16 %1232, ptr %1235, align 2
  %1236 = load ptr, ptr %28, align 4
  %1237 = getelementptr i8, ptr %1236, i32 12
  %1238 = load i16, ptr %1237, align 2
  %1239 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1240 = getelementptr inbounds %struct.Matrix32, ptr %1239, i32 0, i32 0
  %1241 = getelementptr [9 x i16], ptr %1240, i32 0, i32 8
  store i16 %1238, ptr %1241, align 4
  br label %1242

1242:                                             ; preds = %795, %1168, %1093, %1028, %945, %871, %797
  %1243 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %1244 = getelementptr inbounds %struct.Vec3, ptr %1243, i32 0, i32 0
  %1245 = load i32, ptr %1244, align 4
  %1246 = icmp sgt i32 %1245, 0
  br i1 %1246, label %1247, label %1250

1247:                                             ; preds = %1242
  %1248 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %1249 = getelementptr inbounds %struct.Vec3, ptr %1248, i32 0, i32 0
  store i32 0, ptr %1249, align 4
  br label %1281

1250:                                             ; preds = %1242
  %1251 = load i32, ptr %19, align 4
  %1252 = icmp uge i32 %1251, 4
  br i1 %1252, label %1253, label %1280

1253:                                             ; preds = %1250
  %1254 = load ptr, ptr %9, align 4
  %1255 = load i32, ptr %11, align 4
  %1256 = mul i32 %1255, 48
  %1257 = getelementptr i8, ptr %1254, i32 %1256
  store ptr %1257, ptr %40, align 4
  %1258 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %1259 = getelementptr inbounds %struct.Vec3, ptr %1258, i32 0, i32 0
  %1260 = load i32, ptr %1259, align 4
  %1261 = shl i32 %1260, 4
  %1262 = load ptr, ptr %40, align 4
  %1263 = getelementptr i8, ptr %1262, i32 40
  store i32 %1261, ptr %1263, align 4
  %1264 = load i32, ptr %19, align 4
  %1265 = load ptr, ptr %40, align 4
  %1266 = getelementptr i8, ptr %1265, i32 36
  store i32 %1264, ptr %1266, align 4
  %1267 = load ptr, ptr %5, align 4
  %1268 = load ptr, ptr %40, align 4
  %1269 = getelementptr i8, ptr %1268, i32 32
  store ptr %1267, ptr %1269, align 4
  %1270 = load ptr, ptr %40, align 4
  %1271 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  call void @llvm.memcpy.p0.p0.i32(ptr align 4 %1270, ptr align 4 %1271, i32 32, i1 false)
  %1272 = load ptr, ptr %40, align 4
  %1273 = getelementptr i8, ptr %1272, i32 44
  store i32 1, ptr %1273, align 4
  %1274 = load i32, ptr %11, align 4
  %1275 = add i32 %1274, 1
  %1276 = load ptr, ptr %4, align 4
  %1277 = getelementptr i8, ptr %1276, i32 140
  %1278 = load ptr, ptr %1277, align 4
  %1279 = getelementptr i8, ptr %1278, i32 820
  store i32 %1275, ptr %1279, align 4
  br label %1280

1280:                                             ; preds = %1253, %1250
  br label %1281

1281:                                             ; preds = %1280, %1247
  %1282 = load i32, ptr %19, align 4
  %1283 = icmp ult i32 %1282, 4
  br i1 %1283, label %1284, label %1310

1284:                                             ; preds = %1281
  %1285 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %1286 = getelementptr inbounds %struct.Vec3, ptr %1285, i32 0, i32 0
  %1287 = load i32, ptr %1286, align 4
  %1288 = load ptr, ptr %4, align 4
  %1289 = getelementptr i8, ptr %1288, i32 128
  %1290 = load ptr, ptr %1289, align 4
  %1291 = load i32, ptr %19, align 4
  %1292 = mul i32 %1291, 4
  %1293 = add i32 784, %1292
  %1294 = getelementptr i8, ptr %1290, i32 %1293
  store i32 %1287, ptr %1294, align 4
  %1295 = load ptr, ptr %4, align 4
  %1296 = getelementptr i8, ptr %1295, i32 128
  %1297 = load ptr, ptr %1296, align 4
  %1298 = load i32, ptr %19, align 4
  %1299 = mul i32 %1298, 4
  %1300 = add i32 800, %1299
  %1301 = getelementptr i8, ptr %1297, i32 %1300
  store i32 1, ptr %1301, align 4
  %1302 = load ptr, ptr %4, align 4
  %1303 = getelementptr i8, ptr %1302, i32 140
  %1304 = load ptr, ptr %1303, align 4
  %1305 = getelementptr i8, ptr %1304, i32 48
  store ptr %1305, ptr %41, align 4
  %1306 = load ptr, ptr %41, align 4
  %1307 = load i32, ptr %19, align 4
  %1308 = getelementptr %struct.Matrix32, ptr %1306, i32 %1307
  %1309 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  call void @llvm.memcpy.p0.p0.i32(ptr align 4 %1308, ptr align 4 %1309, i32 32, i1 false)
  br label %1310

1310:                                             ; preds = %1284, %1281
  %1311 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %1312 = getelementptr inbounds %struct.Vec3, ptr %1311, i32 0, i32 0
  %1313 = load i32, ptr %1312, align 4
  %1314 = icmp slt i32 %1313, 0
  br i1 %1314, label %1315, label %1431

1315:                                             ; preds = %1310
  %1316 = load i32, ptr %17, align 4
  %1317 = add i32 %1316, 1
  store i32 %1317, ptr %17, align 4
  %1318 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 2
  %1319 = getelementptr inbounds %struct.Vec3, ptr %1318, i32 0, i32 0
  %1320 = load i32, ptr %1319, align 4
  %1321 = shl i32 %1320, 4
  %1322 = load ptr, ptr %30, align 4
  %1323 = getelementptr i8, ptr %1322, i32 48
  store i32 %1321, ptr %1323, align 4
  %1324 = load ptr, ptr %30, align 4
  %1325 = getelementptr i8, ptr %1324, i32 52
  store i32 1, ptr %1325, align 4
  %1326 = load i32, ptr %19, align 4
  %1327 = load ptr, ptr %30, align 4
  %1328 = getelementptr i8, ptr %1327, i32 44
  store i32 %1326, ptr %1328, align 4
  %1329 = load ptr, ptr %4, align 4
  %1330 = load ptr, ptr %30, align 4
  %1331 = getelementptr i8, ptr %1330, i32 40
  store ptr %1329, ptr %1331, align 4
  %1332 = load i32, ptr %18, align 4
  %1333 = icmp eq i32 %1332, 0
  br i1 %1333, label %1334, label %1337

1334:                                             ; preds = %1315
  %1335 = load ptr, ptr %30, align 4
  %1336 = getelementptr i8, ptr %1335, i32 0
  store i16 0, ptr %1336, align 2
  br label %1344

1337:                                             ; preds = %1315
  %1338 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  %1339 = getelementptr inbounds %struct.Vec3, ptr %1338, i32 0, i32 0
  %1340 = load i32, ptr %1339, align 4
  %1341 = trunc i32 %1340 to i16
  %1342 = load ptr, ptr %30, align 4
  %1343 = getelementptr i8, ptr %1342, i32 0
  store i16 %1341, ptr %1343, align 2
  br label %1344

1344:                                             ; preds = %1337, %1334
  %1345 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  %1346 = getelementptr inbounds %struct.Vec3, ptr %1345, i32 0, i32 1
  %1347 = load i32, ptr %1346, align 4
  %1348 = trunc i32 %1347 to i16
  %1349 = load ptr, ptr %30, align 4
  %1350 = getelementptr i8, ptr %1349, i32 2
  store i16 %1348, ptr %1350, align 2
  %1351 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 0
  %1352 = getelementptr inbounds %struct.Vec3, ptr %1351, i32 0, i32 2
  %1353 = load i32, ptr %1352, align 4
  %1354 = trunc i32 %1353 to i16
  %1355 = load ptr, ptr %30, align 4
  %1356 = getelementptr i8, ptr %1355, i32 4
  store i16 %1354, ptr %1356, align 2
  %1357 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1358 = getelementptr inbounds %struct.Matrix32, ptr %1357, i32 0, i32 0
  %1359 = getelementptr [9 x i16], ptr %1358, i32 0, i32 1
  %1360 = load i16, ptr %1359, align 2
  %1361 = zext i16 %1360 to i32
  %1362 = sub i32 0, %1361
  %1363 = trunc i32 %1362 to i16
  %1364 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1365 = getelementptr inbounds %struct.Matrix32, ptr %1364, i32 0, i32 0
  %1366 = getelementptr [9 x i16], ptr %1365, i32 0, i32 1
  store i16 %1363, ptr %1366, align 2
  %1367 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1368 = getelementptr inbounds %struct.Matrix32, ptr %1367, i32 0, i32 0
  %1369 = getelementptr [9 x i16], ptr %1368, i32 0, i32 4
  %1370 = load i16, ptr %1369, align 4
  %1371 = zext i16 %1370 to i32
  %1372 = sub i32 0, %1371
  %1373 = trunc i32 %1372 to i16
  %1374 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1375 = getelementptr inbounds %struct.Matrix32, ptr %1374, i32 0, i32 0
  %1376 = getelementptr [9 x i16], ptr %1375, i32 0, i32 4
  store i16 %1373, ptr %1376, align 4
  %1377 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1378 = getelementptr inbounds %struct.Matrix32, ptr %1377, i32 0, i32 0
  %1379 = getelementptr [9 x i16], ptr %1378, i32 0, i32 7
  %1380 = load i16, ptr %1379, align 2
  %1381 = zext i16 %1380 to i32
  %1382 = sub i32 0, %1381
  %1383 = trunc i32 %1382 to i16
  %1384 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1385 = getelementptr inbounds %struct.Matrix32, ptr %1384, i32 0, i32 0
  %1386 = getelementptr [9 x i16], ptr %1385, i32 0, i32 7
  store i16 %1383, ptr %1386, align 2
  %1387 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1388 = getelementptr inbounds %struct.Matrix32, ptr %1387, i32 0, i32 0
  %1389 = getelementptr [9 x i16], ptr %1388, i32 0, i32 2
  %1390 = load i16, ptr %1389, align 4
  store i16 %1390, ptr %14, align 2
  %1391 = load i16, ptr %14, align 2
  %1392 = zext i16 %1391 to i32
  %1393 = sub i32 0, %1392
  %1394 = trunc i32 %1393 to i16
  %1395 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1396 = getelementptr inbounds %struct.Matrix32, ptr %1395, i32 0, i32 0
  %1397 = getelementptr [9 x i16], ptr %1396, i32 0, i32 2
  store i16 %1394, ptr %1397, align 4
  %1398 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1399 = getelementptr inbounds %struct.Matrix32, ptr %1398, i32 0, i32 0
  %1400 = getelementptr [9 x i16], ptr %1399, i32 0, i32 5
  %1401 = load i16, ptr %1400, align 2
  %1402 = zext i16 %1401 to i32
  %1403 = sub i32 0, %1402
  %1404 = trunc i32 %1403 to i16
  %1405 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1406 = getelementptr inbounds %struct.Matrix32, ptr %1405, i32 0, i32 0
  %1407 = getelementptr [9 x i16], ptr %1406, i32 0, i32 5
  store i16 %1404, ptr %1407, align 2
  %1408 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1409 = getelementptr inbounds %struct.Matrix32, ptr %1408, i32 0, i32 0
  %1410 = getelementptr [9 x i16], ptr %1409, i32 0, i32 8
  %1411 = load i16, ptr %1410, align 4
  %1412 = zext i16 %1411 to i32
  %1413 = sub i32 0, %1412
  %1414 = trunc i32 %1413 to i16
  %1415 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  %1416 = getelementptr inbounds %struct.Matrix32, ptr %1415, i32 0, i32 0
  %1417 = getelementptr [9 x i16], ptr %1416, i32 0, i32 8
  store i16 %1414, ptr %1417, align 4
  %1418 = load ptr, ptr %30, align 4
  %1419 = getelementptr i8, ptr %1418, i32 8
  %1420 = getelementptr inbounds %struct.Scratch, ptr %6, i32 0, i32 6
  call void @llvm.memcpy.p0.p0.i32(ptr align 4 %1419, ptr align 4 %1420, i32 32, i1 false)
  %1421 = load i32, ptr %10, align 4
  %1422 = icmp slt i32 %1421, 10
  br i1 %1422, label %1423, label %1430

1423:                                             ; preds = %1344
  %1424 = load i32, ptr %10, align 4
  %1425 = add i32 %1424, 1
  %1426 = load ptr, ptr %5, align 4
  %1427 = getelementptr i8, ptr %1426, i32 140
  %1428 = load ptr, ptr %1427, align 4
  %1429 = getelementptr i8, ptr %1428, i32 256
  store i32 %1425, ptr %1429, align 4
  br label %1430

1430:                                             ; preds = %1423, %1344
  br label %1431

1431:                                             ; preds = %1430, %1310
  br label %1432

1432:                                             ; preds = %1431, %794, %717
  %1433 = load i32, ptr %19, align 4
  %1434 = add i32 %1433, 1
  store i32 %1434, ptr %19, align 4
  br label %666, !llvm.loop !11

1435:                                             ; preds = %666
  %1436 = load i32, ptr %17, align 4
  %1437 = trunc i32 %1436 to i8
  store i8 %1437, ptr %42, align 1
  %1438 = load i8, ptr %42, align 1
  %1439 = icmp ne i8 %1438, 0
  br i1 %1439, label %1440, label %1448

1440:                                             ; preds = %1435
  %1441 = load i32, ptr %16, align 4
  %1442 = load i8, ptr %42, align 1
  %1443 = zext i8 %1442 to i32
  %1444 = shl i32 %1443, 8
  %1445 = ashr i32 %1444, 16
  %1446 = or i32 %1441, %1445
  %1447 = trunc i32 %1446 to i8
  store i8 %1447, ptr %3, align 1
  br label %1451

1448:                                             ; preds = %1435
  %1449 = load i32, ptr %16, align 4
  %1450 = trunc i32 %1449 to i8
  store i8 %1450, ptr %3, align 1
  br label %1451

1451:                                             ; preds = %1448, %1440
  %1452 = load i8, ptr %3, align 1
  ret i8 %1452
}

declare void @func_8002DA98(ptr noundef, ptr noundef) #1

declare void @func_8002E530(ptr noundef, ptr noundef, ptr noundef) #1

declare void @func_8002D75C(i32 noundef, i32 noundef, i32 noundef, ptr noundef) #1

; Function Attrs: nocallback nofree nounwind willreturn memory(argmem: readwrite)
declare void @llvm.memcpy.p0.p0.i32(ptr noalias nocapture writeonly, ptr noalias nocapture readonly, i32, i1 immarg) #2

declare void @func_8002E778(ptr noundef, ptr noundef) #1

attributes #0 = { noinline nounwind optnone uwtable "frame-pointer"="all" "no-builtins" "no-trapping-math"="true" "stack-protector-buffer-size"="8" "target-cpu"="generic" "target-features"="+bulk-memory,+extended-const,+multivalue,+mutable-globals,+nontrapping-fptoint,+sign-ext,-atomics,-exception-handling,-half-precision,-multimemory,-reference-types,-relaxed-simd,-simd128,-tail-call" }
attributes #1 = { "frame-pointer"="all" "no-builtins" "no-trapping-math"="true" "stack-protector-buffer-size"="8" "target-cpu"="generic" "target-features"="+bulk-memory,+extended-const,+multivalue,+mutable-globals,+nontrapping-fptoint,+sign-ext,-atomics,-exception-handling,-half-precision,-multimemory,-reference-types,-relaxed-simd,-simd128,-tail-call" }
attributes #2 = { nocallback nofree nounwind willreturn memory(argmem: readwrite) }
attributes #3 = { nobuiltin "no-builtins" }

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
!8 = distinct !{!8, !6}
!9 = distinct !{!9, !6}
!10 = distinct !{!10, !6}
!11 = distinct !{!11, !6}
