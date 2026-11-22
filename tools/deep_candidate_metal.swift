// Benchmark helper. Each candidate and target is a pair of MIPS immediate words
// packed into a UInt32. Both backends compute the same Hamming distance.
import Foundation
import Metal

struct Input: Decodable {
    let candidates: [UInt32]
    let targets: [UInt32]
}

struct Output: Encodable {
    let scores: [UInt32]
    let computeSeconds: Double
    let gpuSeconds: Double?
    let device: String?
}

enum RankError: Error {
    case invalidInput, unavailable, allocation, dispatch
}

func score(_ input: Input, backend: String) throws -> Output {
    guard !input.candidates.isEmpty, input.candidates.count == input.targets.count else {
        throw RankError.invalidInput
    }
    if backend == "cpu" {
        let start = ProcessInfo.processInfo.systemUptime
        let scores = zip(input.candidates, input.targets).map { UInt32(($0 ^ $1).nonzeroBitCount) }
        return Output(scores: scores, computeSeconds: ProcessInfo.processInfo.systemUptime - start,
                      gpuSeconds: nil, device: nil)
    }
    guard backend == "metal", let device = MTLCreateSystemDefaultDevice(),
          let queue = device.makeCommandQueue() else { throw RankError.unavailable }
    let library = try device.makeLibrary(source: """
        #include <metal_stdlib>
        using namespace metal;
        kernel void rank_constants(device const uint *candidates [[buffer(0)]],
                                   device const uint *targets [[buffer(1)]],
                                   device uint *scores [[buffer(2)]],
                                   uint i [[thread_position_in_grid]]) {
            scores[i] = popcount(candidates[i] ^ targets[i]);
        }
        """, options: nil)
    guard let function = library.makeFunction(name: "rank_constants") else { throw RankError.unavailable }
    let pipeline = try device.makeComputePipelineState(function: function)
    let start = ProcessInfo.processInfo.systemUptime
    let length = input.candidates.count * MemoryLayout<UInt32>.stride
    guard let candidates = device.makeBuffer(bytes: input.candidates, length: length, options: .storageModeShared),
          let targets = device.makeBuffer(bytes: input.targets, length: length, options: .storageModeShared),
          let scores = device.makeBuffer(length: length, options: .storageModeShared),
          let command = queue.makeCommandBuffer(),
          let encoder = command.makeComputeCommandEncoder() else { throw RankError.allocation }
    encoder.setComputePipelineState(pipeline)
    encoder.setBuffer(candidates, offset: 0, index: 0)
    encoder.setBuffer(targets, offset: 0, index: 1)
    encoder.setBuffer(scores, offset: 0, index: 2)
    encoder.dispatchThreads(MTLSize(width: input.candidates.count, height: 1, depth: 1),
                            threadsPerThreadgroup: MTLSize(width: pipeline.threadExecutionWidth, height: 1, depth: 1))
    encoder.endEncoding()
    command.commit()
    command.waitUntilCompleted()
    guard command.status == .completed else { throw command.error ?? RankError.dispatch }
    let result = Array(UnsafeBufferPointer(start: scores.contents().assumingMemoryBound(to: UInt32.self),
                                           count: input.candidates.count))
    return Output(scores: result, computeSeconds: ProcessInfo.processInfo.systemUptime - start,
                  gpuSeconds: command.gpuEndTime - command.gpuStartTime, device: device.name)
}

guard CommandLine.arguments.count == 2 else { throw RankError.invalidInput }
let input = try JSONDecoder().decode(Input.self, from: FileHandle.standardInput.readDataToEndOfFile())
let output = try score(input, backend: CommandLine.arguments[1])
FileHandle.standardOutput.write(try JSONEncoder().encode(output))
