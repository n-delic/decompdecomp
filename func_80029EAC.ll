; ModuleID = '/private/tmp/claude-501/-Users-a--claude-spares-spare-a2hqyf/ee5497e5-bb0a-4d55-b497-be4e4fae1075/scratchpad/vr/out/src/func_80029EAC.c'
source_filename = "/private/tmp/claude-501/-Users-a--claude-spares-spare-a2hqyf/ee5497e5-bb0a-4d55-b497-be4e4fae1075/scratchpad/vr/out/src/func_80029EAC.c"
target datalayout = "e-m:e-p:32:32-p10:8:8-p20:8:8-i64:64-n32:64-S128-ni:1:10:20"
target triple = "wasm32-unknown-unknown-unknown"

%union.Pair = type { %struct.anon }
%struct.anon = type { i16, i16, i32 }
%struct.Matrix = type { [9 x i16], i16, i32, i32, i32 }
%struct.Vertex = type { i16, i16, i16 }

; Function Attrs: noinline nounwind optnone uwtable
define hidden i32 @func_80029EAC(ptr noundef %0, ptr noundef %1, i32 noundef %2) #0 {
  %4 = alloca i32, align 4
  %5 = alloca ptr, align 4
  %6 = alloca ptr, align 4
  %7 = alloca i32, align 4
  %8 = alloca %union.Pair, align 4
  %9 = alloca %union.Pair, align 4
  %10 = alloca %union.Pair, align 4
  %11 = alloca [4 x i32], align 16
  %12 = alloca [4 x i32], align 16
  %13 = alloca [4 x i32], align 16
  %14 = alloca [4 x i32], align 16
  %15 = alloca [4 x i32], align 16
  %16 = alloca %struct.Matrix, align 2
  %17 = alloca %struct.Matrix, align 2
  %18 = alloca [3 x i16], align 2
  %19 = alloca i32, align 4
  %20 = alloca i32, align 4
  %21 = alloca i32, align 4
  %22 = alloca i32, align 4
  %23 = alloca i32, align 4
  %24 = alloca i32, align 4
  %25 = alloca i32, align 4
  %26 = alloca i32, align 4
  %27 = alloca i32, align 4
  %28 = alloca i32, align 4
  %29 = alloca i16, align 2
  %30 = alloca ptr, align 4
  %31 = alloca ptr, align 4
  %32 = alloca ptr, align 4
  %33 = alloca ptr, align 4
  %34 = alloca i32, align 4
  %35 = alloca i32, align 4
  %36 = alloca i32, align 4
  %37 = alloca i32, align 4
  store ptr %0, ptr %5, align 4
  store ptr %1, ptr %6, align 4
  store i32 %2, ptr %7, align 4
  %38 = load ptr, ptr %5, align 4
  %39 = getelementptr i8, ptr %38, i32 140
  %40 = load ptr, ptr %39, align 4
  %41 = load i32, ptr %7, align 4
  %42 = mul i32 %41, 4
  %43 = getelementptr i8, ptr %40, i32 %42
  %44 = load ptr, ptr %43, align 4
  store ptr %44, ptr %30, align 4
  %45 = load ptr, ptr %5, align 4
  %46 = getelementptr i8, ptr %45, i32 96
  %47 = load ptr, ptr %46, align 4
  %48 = load i32, ptr %7, align 4
  %49 = mul i32 %48, 4
  %50 = getelementptr i8, ptr %47, i32 %49
  %51 = getelementptr i8, ptr %50, i32 0
  %52 = load i32, ptr %51, align 4
  store i32 %52, ptr %20, align 4
  %53 = load ptr, ptr %5, align 4
  %54 = getelementptr i8, ptr %53, i32 96
  %55 = load ptr, ptr %54, align 4
  %56 = load i32, ptr %7, align 4
  %57 = mul i32 %56, 4
  %58 = getelementptr i8, ptr %55, i32 %57
  %59 = getelementptr i8, ptr %58, i32 48
  %60 = load i32, ptr %59, align 4
  store i32 %60, ptr %21, align 4
  %61 = load ptr, ptr %30, align 4
  %62 = getelementptr i8, ptr %61, i32 138
  %63 = load i16, ptr %62, align 2
  store i16 %63, ptr %29, align 2
  %64 = load ptr, ptr %30, align 4
  %65 = getelementptr i8, ptr %64, i32 112
  %66 = load ptr, ptr %65, align 4
  store ptr %66, ptr %31, align 4
  %67 = load i16, ptr %29, align 2
  %68 = zext i16 %67 to i32
  %69 = load i32, ptr %20, align 4
  %70 = mul i32 %68, %69
  %71 = load i32, ptr %21, align 4
  %72 = add i32 %70, %71
  store i32 %72, ptr %22, align 4
  %73 = load ptr, ptr %6, align 4
  %74 = getelementptr i8, ptr %73, i32 0
  %75 = load i16, ptr %74, align 2
  %76 = getelementptr inbounds %struct.anon, ptr %10, i32 0, i32 0
  store i16 %75, ptr %76, align 4
  %77 = load ptr, ptr %6, align 4
  %78 = getelementptr i8, ptr %77, i32 4
  %79 = load i16, ptr %78, align 2
  %80 = getelementptr inbounds %struct.anon, ptr %10, i32 0, i32 1
  store i16 %79, ptr %80, align 2
  %81 = load i32, ptr %22, align 4
  %82 = add i32 %81, 1
  store i32 %82, ptr %23, align 4
  %83 = load ptr, ptr %30, align 4
  %84 = getelementptr i8, ptr %83, i32 84
  %85 = load ptr, ptr %84, align 4
  %86 = load i32, ptr %23, align 4
  %87 = getelementptr %struct.Vertex, ptr %85, i32 %86
  %88 = getelementptr inbounds %struct.Vertex, ptr %87, i32 0, i32 0
  %89 = load i16, ptr %88, align 2
  %90 = getelementptr inbounds %struct.anon, ptr %8, i32 0, i32 0
  store i16 %89, ptr %90, align 4
  %91 = load ptr, ptr %30, align 4
  %92 = getelementptr i8, ptr %91, i32 84
  %93 = load ptr, ptr %92, align 4
  %94 = load i32, ptr %23, align 4
  %95 = getelementptr %struct.Vertex, ptr %93, i32 %94
  %96 = getelementptr inbounds %struct.Vertex, ptr %95, i32 0, i32 2
  %97 = load i16, ptr %96, align 2
  %98 = getelementptr inbounds %struct.anon, ptr %8, i32 0, i32 1
  store i16 %97, ptr %98, align 2
  %99 = load i32, ptr %22, align 4
  %100 = load i16, ptr %29, align 2
  %101 = zext i16 %100 to i32
  %102 = add i32 %99, %101
  store i32 %102, ptr %24, align 4
  %103 = load ptr, ptr %30, align 4
  %104 = getelementptr i8, ptr %103, i32 84
  %105 = load ptr, ptr %104, align 4
  %106 = load i32, ptr %24, align 4
  %107 = getelementptr %struct.Vertex, ptr %105, i32 %106
  %108 = getelementptr inbounds %struct.Vertex, ptr %107, i32 0, i32 0
  %109 = load i16, ptr %108, align 2
  %110 = getelementptr inbounds %struct.anon, ptr %9, i32 0, i32 0
  store i16 %109, ptr %110, align 4
  %111 = load ptr, ptr %30, align 4
  %112 = getelementptr i8, ptr %111, i32 84
  %113 = load ptr, ptr %112, align 4
  %114 = load i32, ptr %24, align 4
  %115 = getelementptr %struct.Vertex, ptr %113, i32 %114
  %116 = getelementptr inbounds %struct.Vertex, ptr %115, i32 0, i32 2
  %117 = load i16, ptr %116, align 2
  %118 = getelementptr inbounds %struct.anon, ptr %9, i32 0, i32 1
  store i16 %117, ptr %118, align 2
  %119 = load i32, ptr %20, align 4
  %120 = load i16, ptr %29, align 2
  %121 = zext i16 %120 to i32
  %122 = sub i32 %121, 1
  %123 = mul i32 %119, %122
  %124 = load i32, ptr %21, align 4
  %125 = add i32 %123, %124
  store i32 %125, ptr %28, align 4
  %126 = load i32, ptr %8, align 4
  %127 = load i32, ptr %9, align 4
  %128 = load i32, ptr %10, align 4
  call void @func_8002D75C(i32 noundef %126, i32 noundef %127, i32 noundef %128, ptr noundef %19) #3
  %129 = load i32, ptr %24, align 4
  store i32 %129, ptr %26, align 4
  %130 = load i32, ptr %26, align 4
  %131 = add i32 %130, 1
  store i32 %131, ptr %26, align 4
  %132 = load i32, ptr %23, align 4
  store i32 %132, ptr %25, align 4
  %133 = load i32, ptr %19, align 4
  %134 = icmp slt i32 %133, 0
  br i1 %134, label %135, label %139

135:                                              ; preds = %3
  %136 = load i32, ptr %26, align 4
  store i32 %136, ptr %22, align 4
  %137 = load i32, ptr %24, align 4
  store i32 %137, ptr %23, align 4
  %138 = load i32, ptr %25, align 4
  store i32 %138, ptr %24, align 4
  br label %139

139:                                              ; preds = %135, %3
  %140 = load ptr, ptr %30, align 4
  %141 = getelementptr i8, ptr %140, i32 84
  %142 = load ptr, ptr %141, align 4
  %143 = load i32, ptr %23, align 4
  %144 = getelementptr %struct.Vertex, ptr %142, i32 %143
  %145 = getelementptr inbounds %struct.Vertex, ptr %144, i32 0, i32 0
  %146 = load i16, ptr %145, align 2
  %147 = sext i16 %146 to i32
  %148 = load ptr, ptr %30, align 4
  %149 = getelementptr i8, ptr %148, i32 84
  %150 = load ptr, ptr %149, align 4
  %151 = load i32, ptr %22, align 4
  %152 = getelementptr %struct.Vertex, ptr %150, i32 %151
  %153 = getelementptr inbounds %struct.Vertex, ptr %152, i32 0, i32 0
  %154 = load i16, ptr %153, align 2
  %155 = sext i16 %154 to i32
  %156 = sub i32 %147, %155
  %157 = getelementptr [4 x i32], ptr %14, i32 0, i32 0
  store i32 %156, ptr %157, align 16
  %158 = load ptr, ptr %30, align 4
  %159 = getelementptr i8, ptr %158, i32 84
  %160 = load ptr, ptr %159, align 4
  %161 = load i32, ptr %23, align 4
  %162 = getelementptr %struct.Vertex, ptr %160, i32 %161
  %163 = getelementptr inbounds %struct.Vertex, ptr %162, i32 0, i32 1
  %164 = load i16, ptr %163, align 2
  %165 = sext i16 %164 to i32
  %166 = load ptr, ptr %30, align 4
  %167 = getelementptr i8, ptr %166, i32 84
  %168 = load ptr, ptr %167, align 4
  %169 = load i32, ptr %22, align 4
  %170 = getelementptr %struct.Vertex, ptr %168, i32 %169
  %171 = getelementptr inbounds %struct.Vertex, ptr %170, i32 0, i32 1
  %172 = load i16, ptr %171, align 2
  %173 = sext i16 %172 to i32
  %174 = sub i32 %165, %173
  %175 = getelementptr [4 x i32], ptr %14, i32 0, i32 1
  store i32 %174, ptr %175, align 4
  %176 = load ptr, ptr %30, align 4
  %177 = getelementptr i8, ptr %176, i32 84
  %178 = load ptr, ptr %177, align 4
  %179 = load i32, ptr %23, align 4
  %180 = getelementptr %struct.Vertex, ptr %178, i32 %179
  %181 = getelementptr inbounds %struct.Vertex, ptr %180, i32 0, i32 2
  %182 = load i16, ptr %181, align 2
  %183 = sext i16 %182 to i32
  %184 = load ptr, ptr %30, align 4
  %185 = getelementptr i8, ptr %184, i32 84
  %186 = load ptr, ptr %185, align 4
  %187 = load i32, ptr %22, align 4
  %188 = getelementptr %struct.Vertex, ptr %186, i32 %187
  %189 = getelementptr inbounds %struct.Vertex, ptr %188, i32 0, i32 2
  %190 = load i16, ptr %189, align 2
  %191 = sext i16 %190 to i32
  %192 = sub i32 %183, %191
  %193 = getelementptr [4 x i32], ptr %14, i32 0, i32 2
  store i32 %192, ptr %193, align 8
  %194 = load ptr, ptr %30, align 4
  %195 = getelementptr i8, ptr %194, i32 84
  %196 = load ptr, ptr %195, align 4
  %197 = load i32, ptr %24, align 4
  %198 = getelementptr %struct.Vertex, ptr %196, i32 %197
  %199 = getelementptr inbounds %struct.Vertex, ptr %198, i32 0, i32 0
  %200 = load i16, ptr %199, align 2
  %201 = sext i16 %200 to i32
  %202 = load ptr, ptr %30, align 4
  %203 = getelementptr i8, ptr %202, i32 84
  %204 = load ptr, ptr %203, align 4
  %205 = load i32, ptr %22, align 4
  %206 = getelementptr %struct.Vertex, ptr %204, i32 %205
  %207 = getelementptr inbounds %struct.Vertex, ptr %206, i32 0, i32 0
  %208 = load i16, ptr %207, align 2
  %209 = sext i16 %208 to i32
  %210 = sub i32 %201, %209
  %211 = getelementptr [4 x i32], ptr %12, i32 0, i32 0
  store i32 %210, ptr %211, align 16
  %212 = load ptr, ptr %30, align 4
  %213 = getelementptr i8, ptr %212, i32 84
  %214 = load ptr, ptr %213, align 4
  %215 = load i32, ptr %24, align 4
  %216 = getelementptr %struct.Vertex, ptr %214, i32 %215
  %217 = getelementptr inbounds %struct.Vertex, ptr %216, i32 0, i32 1
  %218 = load i16, ptr %217, align 2
  %219 = sext i16 %218 to i32
  %220 = load ptr, ptr %30, align 4
  %221 = getelementptr i8, ptr %220, i32 84
  %222 = load ptr, ptr %221, align 4
  %223 = load i32, ptr %22, align 4
  %224 = getelementptr %struct.Vertex, ptr %222, i32 %223
  %225 = getelementptr inbounds %struct.Vertex, ptr %224, i32 0, i32 1
  %226 = load i16, ptr %225, align 2
  %227 = sext i16 %226 to i32
  %228 = sub i32 %219, %227
  %229 = getelementptr [4 x i32], ptr %12, i32 0, i32 1
  store i32 %228, ptr %229, align 4
  %230 = load ptr, ptr %30, align 4
  %231 = getelementptr i8, ptr %230, i32 84
  %232 = load ptr, ptr %231, align 4
  %233 = load i32, ptr %24, align 4
  %234 = getelementptr %struct.Vertex, ptr %232, i32 %233
  %235 = getelementptr inbounds %struct.Vertex, ptr %234, i32 0, i32 2
  %236 = load i16, ptr %235, align 2
  %237 = sext i16 %236 to i32
  %238 = load ptr, ptr %30, align 4
  %239 = getelementptr i8, ptr %238, i32 84
  %240 = load ptr, ptr %239, align 4
  %241 = load i32, ptr %22, align 4
  %242 = getelementptr %struct.Vertex, ptr %240, i32 %241
  %243 = getelementptr inbounds %struct.Vertex, ptr %242, i32 0, i32 2
  %244 = load i16, ptr %243, align 2
  %245 = sext i16 %244 to i32
  %246 = sub i32 %237, %245
  %247 = getelementptr [4 x i32], ptr %12, i32 0, i32 2
  store i32 %246, ptr %247, align 8
  %248 = load i32, ptr %7, align 4
  %249 = sub i32 %248, 4
  %250 = icmp ult i32 %249, 2
  %251 = zext i1 %250 to i32
  %252 = load i32, ptr %7, align 4
  %253 = icmp eq i32 %252, 10
  %254 = zext i1 %253 to i32
  %255 = or i32 %251, %254
  %256 = icmp ne i32 %255, 0
  br i1 %256, label %260, label %257

257:                                              ; preds = %139
  %258 = load i32, ptr %7, align 4
  %259 = icmp eq i32 %258, 11
  br i1 %259, label %260, label %415

260:                                              ; preds = %257, %139
  store i32 0, ptr %25, align 4
  %261 = getelementptr inbounds [4 x i32], ptr %14, i32 0, i32 0
  %262 = getelementptr inbounds [4 x i32], ptr %12, i32 0, i32 0
  %263 = getelementptr inbounds [4 x i32], ptr %15, i32 0, i32 0
  call void @cross_product(ptr noundef %261, ptr noundef %262, ptr noundef %263) #3
  %264 = load i32, ptr %7, align 4
  %265 = icmp eq i32 %264, 4
  %266 = zext i1 %265 to i32
  %267 = mul i32 %266, 2
  store i32 %267, ptr %26, align 4
  %268 = load i32, ptr %7, align 4
  %269 = icmp eq i32 %268, 5
  br i1 %269, label %270, label %271

270:                                              ; preds = %260
  store i32 3, ptr %26, align 4
  br label %271

271:                                              ; preds = %270, %260
  %272 = load i32, ptr %7, align 4
  %273 = icmp ne i32 %272, 10
  %274 = zext i1 %273 to i32
  %275 = sub i32 0, %274
  %276 = load i32, ptr %26, align 4
  %277 = and i32 %276, %275
  store i32 %277, ptr %26, align 4
  %278 = load i32, ptr %7, align 4
  %279 = icmp eq i32 %278, 11
  br i1 %279, label %280, label %281

280:                                              ; preds = %271
  store i32 1, ptr %26, align 4
  br label %281

281:                                              ; preds = %280, %271
  %282 = getelementptr [4 x i32], ptr %15, i32 0, i32 0
  %283 = load i32, ptr %282, align 16
  call void @func_8002D7E4(i32 noundef %283, ptr noundef %34) #3
  %284 = getelementptr [4 x i32], ptr %15, i32 0, i32 1
  %285 = load i32, ptr %284, align 4
  call void @func_8002D7E4(i32 noundef %285, ptr noundef %35) #3
  %286 = getelementptr [4 x i32], ptr %15, i32 0, i32 2
  %287 = load i32, ptr %286, align 8
  call void @func_8002D7E4(i32 noundef %287, ptr noundef %36) #3
  %288 = load i32, ptr %34, align 4
  %289 = icmp slt i32 %288, 16
  br i1 %289, label %296, label %290

290:                                              ; preds = %281
  %291 = load i32, ptr %35, align 4
  %292 = icmp slt i32 %291, 16
  br i1 %292, label %296, label %293

293:                                              ; preds = %290
  %294 = load i32, ptr %36, align 4
  %295 = icmp slt i32 %294, 16
  br i1 %295, label %296, label %323

296:                                              ; preds = %293, %290, %281
  %297 = load i32, ptr %34, align 4
  %298 = load i32, ptr %35, align 4
  %299 = icmp sgt i32 %297, %298
  br i1 %299, label %300, label %302

300:                                              ; preds = %296
  %301 = load i32, ptr %35, align 4
  store i32 %301, ptr %34, align 4
  br label %302

302:                                              ; preds = %300, %296
  %303 = load i32, ptr %34, align 4
  %304 = load i32, ptr %36, align 4
  %305 = icmp sgt i32 %303, %304
  br i1 %305, label %306, label %308

306:                                              ; preds = %302
  %307 = load i32, ptr %36, align 4
  store i32 %307, ptr %34, align 4
  br label %308

308:                                              ; preds = %306, %302
  %309 = load i32, ptr %34, align 4
  %310 = sub i32 17, %309
  store i32 %310, ptr %34, align 4
  %311 = load i32, ptr %34, align 4
  %312 = getelementptr [4 x i32], ptr %15, i32 0, i32 0
  %313 = load i32, ptr %312, align 16
  %314 = ashr i32 %313, %311
  store i32 %314, ptr %312, align 16
  %315 = load i32, ptr %34, align 4
  %316 = getelementptr [4 x i32], ptr %15, i32 0, i32 1
  %317 = load i32, ptr %316, align 4
  %318 = ashr i32 %317, %315
  store i32 %318, ptr %316, align 4
  %319 = load i32, ptr %34, align 4
  %320 = getelementptr [4 x i32], ptr %15, i32 0, i32 2
  %321 = load i32, ptr %320, align 8
  %322 = ashr i32 %321, %319
  store i32 %322, ptr %320, align 8
  br label %323

323:                                              ; preds = %308, %293
  %324 = load ptr, ptr %30, align 4
  %325 = getelementptr i8, ptr %324, i32 84
  %326 = load ptr, ptr %325, align 4
  store ptr %326, ptr %33, align 4
  %327 = load ptr, ptr %33, align 4
  %328 = load i32, ptr %22, align 4
  %329 = getelementptr %struct.Vertex, ptr %327, i32 %328
  %330 = getelementptr inbounds %struct.Vertex, ptr %329, i32 0, i32 0
  %331 = load i16, ptr %330, align 2
  %332 = sext i16 %331 to i32
  %333 = getelementptr [4 x i32], ptr %15, i32 0, i32 0
  %334 = load i32, ptr %333, align 16
  %335 = mul i32 %332, %334
  %336 = load ptr, ptr %33, align 4
  %337 = load i32, ptr %22, align 4
  %338 = getelementptr %struct.Vertex, ptr %336, i32 %337
  %339 = getelementptr inbounds %struct.Vertex, ptr %338, i32 0, i32 1
  %340 = load i16, ptr %339, align 2
  %341 = sext i16 %340 to i32
  %342 = getelementptr [4 x i32], ptr %15, i32 0, i32 1
  %343 = load i32, ptr %342, align 4
  %344 = mul i32 %341, %343
  %345 = add i32 %335, %344
  %346 = load ptr, ptr %33, align 4
  %347 = load i32, ptr %22, align 4
  %348 = getelementptr %struct.Vertex, ptr %346, i32 %347
  %349 = getelementptr inbounds %struct.Vertex, ptr %348, i32 0, i32 2
  %350 = load i16, ptr %349, align 2
  %351 = sext i16 %350 to i32
  %352 = getelementptr [4 x i32], ptr %15, i32 0, i32 2
  %353 = load i32, ptr %352, align 8
  %354 = mul i32 %351, %353
  %355 = add i32 %345, %354
  store i32 %355, ptr %27, align 4
  br label %356

356:                                              ; preds = %323
  %357 = getelementptr [4 x i32], ptr %15, i32 0, i32 1
  %358 = load i32, ptr %357, align 4
  %359 = icmp ne i32 %358, 0
  br i1 %359, label %360, label %383

360:                                              ; preds = %356
  %361 = getelementptr [4 x i32], ptr %15, i32 0, i32 2
  %362 = load i32, ptr %361, align 8
  store i32 %362, ptr %25, align 4
  br label %363

363:                                              ; preds = %360
  %364 = getelementptr [4 x i32], ptr %15, i32 0, i32 0
  %365 = load i32, ptr %364, align 16
  %366 = sub i32 0, %365
  %367 = getelementptr inbounds %struct.anon, ptr %10, i32 0, i32 0
  %368 = load i16, ptr %367, align 4
  %369 = sext i16 %368 to i32
  %370 = mul i32 %366, %369
  %371 = load i32, ptr %25, align 4
  %372 = getelementptr inbounds %struct.anon, ptr %10, i32 0, i32 1
  %373 = load i16, ptr %372, align 2
  %374 = sext i16 %373 to i32
  %375 = mul i32 %371, %374
  %376 = sub i32 %370, %375
  %377 = load i32, ptr %27, align 4
  %378 = add i32 %376, %377
  %379 = getelementptr [4 x i32], ptr %15, i32 0, i32 1
  %380 = load i32, ptr %379, align 4
  %381 = sdiv i32 %378, %380
  store i32 %381, ptr %25, align 4
  br label %382

382:                                              ; preds = %363
  br label %383

383:                                              ; preds = %382, %356
  br label %384

384:                                              ; preds = %383
  %385 = load ptr, ptr %6, align 4
  %386 = getelementptr i8, ptr %385, i32 0
  %387 = load i16, ptr %386, align 2
  %388 = sext i16 %387 to i32
  %389 = load ptr, ptr %5, align 4
  %390 = getelementptr i8, ptr %389, i32 140
  %391 = load ptr, ptr %390, align 4
  %392 = load i32, ptr %26, align 4
  %393 = mul i32 %392, 20
  %394 = add i32 %393, 176
  %395 = getelementptr i8, ptr %391, i32 %394
  store i32 %388, ptr %395, align 4
  %396 = load i32, ptr %25, align 4
  %397 = load ptr, ptr %5, align 4
  %398 = getelementptr i8, ptr %397, i32 140
  %399 = load ptr, ptr %398, align 4
  %400 = load i32, ptr %26, align 4
  %401 = mul i32 %400, 20
  %402 = add i32 %401, 180
  %403 = getelementptr i8, ptr %399, i32 %402
  store i32 %396, ptr %403, align 4
  %404 = load ptr, ptr %6, align 4
  %405 = getelementptr i8, ptr %404, i32 4
  %406 = load i16, ptr %405, align 2
  %407 = sext i16 %406 to i32
  %408 = load ptr, ptr %5, align 4
  %409 = getelementptr i8, ptr %408, i32 140
  %410 = load ptr, ptr %409, align 4
  %411 = load i32, ptr %26, align 4
  %412 = mul i32 %411, 20
  %413 = add i32 %412, 184
  %414 = getelementptr i8, ptr %410, i32 %413
  store i32 %407, ptr %414, align 4
  br label %415

415:                                              ; preds = %384, %257
  %416 = getelementptr inbounds [4 x i32], ptr %14, i32 0, i32 0
  %417 = getelementptr inbounds [4 x i32], ptr %14, i32 0, i32 0
  %418 = call i32 @vec3_normalize_fixed(ptr noundef %416, ptr noundef %417) #3
  %419 = getelementptr inbounds [4 x i32], ptr %12, i32 0, i32 0
  %420 = getelementptr inbounds [4 x i32], ptr %12, i32 0, i32 0
  %421 = call i32 @vec3_normalize_fixed(ptr noundef %419, ptr noundef %420) #3
  %422 = getelementptr inbounds [4 x i32], ptr %14, i32 0, i32 0
  %423 = getelementptr inbounds [4 x i32], ptr %12, i32 0, i32 0
  %424 = getelementptr inbounds [4 x i32], ptr %13, i32 0, i32 0
  call void @func_8002D740(ptr noundef %422, ptr noundef %423, ptr noundef %424) #3
  %425 = getelementptr inbounds [4 x i32], ptr %12, i32 0, i32 0
  %426 = getelementptr inbounds [4 x i32], ptr %13, i32 0, i32 0
  %427 = getelementptr inbounds [4 x i32], ptr %14, i32 0, i32 0
  call void @func_8002D740(ptr noundef %425, ptr noundef %426, ptr noundef %427) #3
  %428 = getelementptr [4 x i32], ptr %12, i32 0, i32 0
  %429 = load i32, ptr %428, align 16
  %430 = trunc i32 %429 to i16
  %431 = getelementptr inbounds %struct.Matrix, ptr %16, i32 0, i32 0
  %432 = getelementptr [9 x i16], ptr %431, i32 0, i32 0
  store i16 %430, ptr %432, align 2
  %433 = getelementptr [4 x i32], ptr %12, i32 0, i32 1
  %434 = load i32, ptr %433, align 4
  %435 = trunc i32 %434 to i16
  %436 = getelementptr inbounds %struct.Matrix, ptr %16, i32 0, i32 0
  %437 = getelementptr [9 x i16], ptr %436, i32 0, i32 1
  store i16 %435, ptr %437, align 2
  %438 = getelementptr [4 x i32], ptr %12, i32 0, i32 2
  %439 = load i32, ptr %438, align 8
  %440 = trunc i32 %439 to i16
  %441 = getelementptr inbounds %struct.Matrix, ptr %16, i32 0, i32 0
  %442 = getelementptr [9 x i16], ptr %441, i32 0, i32 2
  store i16 %440, ptr %442, align 2
  %443 = getelementptr [4 x i32], ptr %13, i32 0, i32 0
  %444 = load i32, ptr %443, align 16
  %445 = trunc i32 %444 to i16
  %446 = getelementptr inbounds %struct.Matrix, ptr %16, i32 0, i32 0
  %447 = getelementptr [9 x i16], ptr %446, i32 0, i32 3
  store i16 %445, ptr %447, align 2
  %448 = getelementptr [4 x i32], ptr %13, i32 0, i32 1
  %449 = load i32, ptr %448, align 4
  %450 = trunc i32 %449 to i16
  %451 = getelementptr inbounds %struct.Matrix, ptr %16, i32 0, i32 0
  %452 = getelementptr [9 x i16], ptr %451, i32 0, i32 4
  store i16 %450, ptr %452, align 2
  %453 = getelementptr [4 x i32], ptr %13, i32 0, i32 2
  %454 = load i32, ptr %453, align 8
  %455 = trunc i32 %454 to i16
  %456 = getelementptr inbounds %struct.Matrix, ptr %16, i32 0, i32 0
  %457 = getelementptr [9 x i16], ptr %456, i32 0, i32 5
  store i16 %455, ptr %457, align 2
  %458 = getelementptr [4 x i32], ptr %14, i32 0, i32 0
  %459 = load i32, ptr %458, align 16
  %460 = trunc i32 %459 to i16
  %461 = getelementptr inbounds %struct.Matrix, ptr %16, i32 0, i32 0
  %462 = getelementptr [9 x i16], ptr %461, i32 0, i32 6
  store i16 %460, ptr %462, align 2
  %463 = getelementptr [4 x i32], ptr %14, i32 0, i32 1
  %464 = load i32, ptr %463, align 4
  %465 = trunc i32 %464 to i16
  %466 = getelementptr inbounds %struct.Matrix, ptr %16, i32 0, i32 0
  %467 = getelementptr [9 x i16], ptr %466, i32 0, i32 7
  store i16 %465, ptr %467, align 2
  %468 = getelementptr [4 x i32], ptr %14, i32 0, i32 2
  %469 = load i32, ptr %468, align 8
  %470 = trunc i32 %469 to i16
  %471 = getelementptr inbounds %struct.Matrix, ptr %16, i32 0, i32 0
  %472 = getelementptr [9 x i16], ptr %471, i32 0, i32 8
  store i16 %470, ptr %472, align 2
  %473 = load ptr, ptr %6, align 4
  %474 = getelementptr i8, ptr %473, i32 0
  %475 = load i16, ptr %474, align 2
  %476 = sext i16 %475 to i32
  %477 = load ptr, ptr %30, align 4
  %478 = getelementptr i8, ptr %477, i32 84
  %479 = load ptr, ptr %478, align 4
  %480 = load i32, ptr %22, align 4
  %481 = getelementptr %struct.Vertex, ptr %479, i32 %480
  %482 = getelementptr inbounds %struct.Vertex, ptr %481, i32 0, i32 0
  %483 = load i16, ptr %482, align 2
  %484 = sext i16 %483 to i32
  %485 = sub i32 %476, %484
  %486 = trunc i32 %485 to i16
  %487 = getelementptr [3 x i16], ptr %18, i32 0, i32 0
  store i16 %486, ptr %487, align 2
  %488 = load ptr, ptr %6, align 4
  %489 = getelementptr i8, ptr %488, i32 2
  %490 = load i16, ptr %489, align 2
  %491 = sext i16 %490 to i32
  %492 = load ptr, ptr %30, align 4
  %493 = getelementptr i8, ptr %492, i32 84
  %494 = load ptr, ptr %493, align 4
  %495 = load i32, ptr %22, align 4
  %496 = getelementptr %struct.Vertex, ptr %494, i32 %495
  %497 = getelementptr inbounds %struct.Vertex, ptr %496, i32 0, i32 1
  %498 = load i16, ptr %497, align 2
  %499 = sext i16 %498 to i32
  %500 = sub i32 %491, %499
  %501 = trunc i32 %500 to i16
  %502 = getelementptr [3 x i16], ptr %18, i32 0, i32 1
  store i16 %501, ptr %502, align 2
  %503 = load ptr, ptr %6, align 4
  %504 = getelementptr i8, ptr %503, i32 4
  %505 = load i16, ptr %504, align 2
  %506 = sext i16 %505 to i32
  %507 = load ptr, ptr %30, align 4
  %508 = getelementptr i8, ptr %507, i32 84
  %509 = load ptr, ptr %508, align 4
  %510 = load i32, ptr %22, align 4
  %511 = getelementptr %struct.Vertex, ptr %509, i32 %510
  %512 = getelementptr inbounds %struct.Vertex, ptr %511, i32 0, i32 2
  %513 = load i16, ptr %512, align 2
  %514 = sext i16 %513 to i32
  %515 = sub i32 %506, %514
  %516 = trunc i32 %515 to i16
  %517 = getelementptr [3 x i16], ptr %18, i32 0, i32 2
  store i16 %516, ptr %517, align 2
  %518 = getelementptr inbounds %struct.Matrix, ptr %16, i32 0, i32 0
  %519 = getelementptr inbounds [9 x i16], ptr %518, i32 0, i32 0
  %520 = getelementptr inbounds [3 x i16], ptr %18, i32 0, i32 0
  %521 = getelementptr inbounds [4 x i32], ptr %11, i32 0, i32 0
  %522 = call ptr @func_8002E530(ptr noundef %519, ptr noundef %520, ptr noundef %521) #3
  %523 = call ptr @func_8002DA98(ptr noundef %16, ptr noundef %17) #3
  call void @llvm.memcpy.p0.p0.i32(ptr align 2 %16, ptr align 2 %17, i32 32, i1 false)
  %524 = getelementptr [4 x i32], ptr %11, i32 0, i32 1
  %525 = load i32, ptr %524, align 4
  %526 = mul i32 %525, 16
  store i32 %526, ptr %37, align 4
  %527 = load i32, ptr %37, align 4
  %528 = icmp sgt i32 %527, 0
  br i1 %528, label %529, label %531

529:                                              ; preds = %415
  %530 = load i32, ptr %37, align 4
  store i32 %530, ptr %4, align 4
  br label %568

531:                                              ; preds = %415
  %532 = load i32, ptr %37, align 4
  store i32 %532, ptr %26, align 4
  %533 = load ptr, ptr %5, align 4
  %534 = getelementptr i8, ptr %533, i32 140
  %535 = load ptr, ptr %534, align 4
  %536 = getelementptr i8, ptr %535, i32 824
  store ptr %536, ptr %32, align 4
  %537 = load ptr, ptr %5, align 4
  %538 = getelementptr i8, ptr %537, i32 140
  %539 = load ptr, ptr %538, align 4
  %540 = getelementptr i8, ptr %539, i32 820
  %541 = load i32, ptr %540, align 4
  %542 = mul i32 %541, 48
  %543 = load ptr, ptr %32, align 4
  %544 = getelementptr i8, ptr %543, i32 %542
  store ptr %544, ptr %32, align 4
  %545 = load i32, ptr %26, align 4
  %546 = load ptr, ptr %32, align 4
  %547 = getelementptr i8, ptr %546, i32 40
  store i32 %545, ptr %547, align 4
  %548 = load i32, ptr %7, align 4
  %549 = load ptr, ptr %32, align 4
  %550 = getelementptr i8, ptr %549, i32 36
  store i32 %548, ptr %550, align 4
  %551 = load ptr, ptr %32, align 4
  %552 = getelementptr i8, ptr %551, i32 32
  store i32 0, ptr %552, align 4
  %553 = load ptr, ptr %32, align 4
  call void @llvm.memcpy.p0.p0.i32(ptr align 2 %553, ptr align 2 %16, i32 32, i1 false)
  %554 = load ptr, ptr %31, align 4
  %555 = load i32, ptr %28, align 4
  %556 = mul i32 %555, 2
  %557 = getelementptr i8, ptr %554, i32 %556
  %558 = load i16, ptr %557, align 2
  %559 = zext i16 %558 to i32
  %560 = load ptr, ptr %32, align 4
  %561 = getelementptr i8, ptr %560, i32 44
  store i32 %559, ptr %561, align 4
  %562 = load ptr, ptr %5, align 4
  %563 = getelementptr i8, ptr %562, i32 140
  %564 = load ptr, ptr %563, align 4
  %565 = getelementptr i8, ptr %564, i32 820
  %566 = load i32, ptr %565, align 4
  %567 = add i32 %566, 1
  store i32 %567, ptr %565, align 4
  br label %568

568:                                              ; preds = %531, %529
  %569 = load i32, ptr %4, align 4
  ret i32 %569
}

declare void @func_8002D75C(i32 noundef, i32 noundef, i32 noundef, ptr noundef) #1

declare void @cross_product(ptr noundef, ptr noundef, ptr noundef) #1

declare void @func_8002D7E4(i32 noundef, ptr noundef) #1

declare i32 @vec3_normalize_fixed(ptr noundef, ptr noundef) #1

declare void @func_8002D740(ptr noundef, ptr noundef, ptr noundef) #1

declare ptr @func_8002E530(ptr noundef, ptr noundef, ptr noundef) #1

declare ptr @func_8002DA98(ptr noundef, ptr noundef) #1

; Function Attrs: nocallback nofree nounwind willreturn memory(argmem: readwrite)
declare void @llvm.memcpy.p0.p0.i32(ptr noalias nocapture writeonly, ptr noalias nocapture readonly, i32, i1 immarg) #2

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
