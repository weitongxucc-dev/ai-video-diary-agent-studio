import Foundation
import ImageIO
import Vision

struct FrameInput: Decodable {
    let id: String
    let path: String
    let timestampSeconds: Double
}
struct RequestInput: Decodable { let frames: [FrameInput] }
struct TextObservation: Encodable {
    let text: String
    let confidence: Float
    let box: [Double]
}
struct FrameOutput: Encodable {
    let id: String
    let timestampSeconds: Double
    let width: Int
    let height: Int
    let observations: [TextObservation]
}
struct ResponseOutput: Encodable {
    let modelId: String
    let coordinateSpace = "normalized_top_left"
    let frames: [FrameOutput]
}
enum OCRFailure: Error { case invalidArguments, unreadableImage, invalidFrame }

do {
    let arguments = CommandLine.arguments
    guard arguments.count == 5, arguments[1] == "--request", arguments[3] == "--response" else {
        throw OCRFailure.invalidArguments
    }
    let decoder = JSONDecoder()
    decoder.keyDecodingStrategy = .convertFromSnakeCase
    let input = try decoder.decode(RequestInput.self, from: Data(contentsOf: URL(fileURLWithPath: arguments[2])))
    var frames: [FrameOutput] = []
    for frame in input.frames {
        guard frame.timestampSeconds.isFinite, frame.timestampSeconds >= 0 else { throw OCRFailure.invalidFrame }
        let output = try autoreleasepool { () throws -> FrameOutput in
            guard let source = CGImageSourceCreateWithURL(URL(fileURLWithPath: frame.path) as CFURL, nil),
                  let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { throw OCRFailure.unreadableImage }
            let request = VNRecognizeTextRequest()
            request.revision = VNRecognizeTextRequestRevision3
            request.recognitionLevel = .accurate
            request.recognitionLanguages = ["zh-Hans", "en-US"]
            request.automaticallyDetectsLanguage = true
            request.usesLanguageCorrection = false
            try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
            let observations = (request.results ?? []).compactMap { observation -> TextObservation? in
                guard let text = observation.topCandidates(1).first else { return nil }
                let box = observation.boundingBox
                return TextObservation(text: text.string, confidence: text.confidence,
                    box: [box.minX, 1 - box.maxY, box.width, box.height])
            }
            return FrameOutput(id: frame.id, timestampSeconds: frame.timestampSeconds,
                               width: image.width, height: image.height, observations: observations)
        }
        frames.append(output)
    }
    let version = ProcessInfo.processInfo.operatingSystemVersion
    let output = ResponseOutput(modelId: "apple-vision-text-r3-macos-\(version.majorVersion).\(version.minorVersion).\(version.patchVersion)", frames: frames)
    let encoder = JSONEncoder()
    encoder.keyEncodingStrategy = .convertToSnakeCase
    try encoder.encode(output).write(to: URL(fileURLWithPath: arguments[4]), options: .atomic)
} catch {
    FileHandle.standardError.write(Data("本地 OCR 执行失败：\(error)\n".utf8))
    exit(1)
}
