import Foundation
import AVFoundation
import CoreImage
import Vision

struct Input: Decodable {
    let sourcePath: String
    let outputPath: String
    let strength: Double
    let skinExposure: Double
}
struct Output: Encodable {
    let frameCount: Int
    let faceFrameCount: Int
    let durationSeconds: Double
    let engine = "apple-vision-coreimage-portrait-v1"
}
enum PortraitFailure: Error { case invalidArguments, invalidVideo, reader, writer, image, noFace }

func faceMask(_ faces: [VNFaceObservation], width: Int, height: Int) throws -> CIImage {
    guard let context=CGContext(data:nil,width:width,height:height,bitsPerComponent:8,bytesPerRow:width,
                               space:CGColorSpaceCreateDeviceGray(),bitmapInfo:CGImageAlphaInfo.none.rawValue) else {throw PortraitFailure.image}
    let canvas=CGRect(x:0,y:0,width:width,height:height)
    context.setFillColor(gray:0,alpha:1);context.fill(canvas)
    for face in faces {
        let box=CGRect(x:face.boundingBox.minX*Double(width),y:face.boundingBox.minY*Double(height),
                       width:face.boundingBox.width*Double(width),height:face.boundingBox.height*Double(height))
        context.setFillColor(gray:1,alpha:1)
        context.fillEllipse(in:box.insetBy(dx:box.width*0.09,dy:box.height*0.04))
        context.setFillColor(gray:0,alpha:1)
        // Preserve expression, lip motion, brows, and glasses/eye detail.
        for region in [face.landmarks?.leftEye,face.landmarks?.rightEye,face.landmarks?.leftEyebrow,
                       face.landmarks?.rightEyebrow,face.landmarks?.outerLips,face.landmarks?.nose] {
            guard let points=region?.normalizedPoints, !points.isEmpty else {continue}
            let xs=points.map{box.minX+CGFloat($0.x)*box.width}
            let ys=points.map{box.minY+CGFloat($0.y)*box.height}
            let rect=CGRect(x:xs.min()!,y:ys.min()!,width:xs.max()!-xs.min()!,height:ys.max()!-ys.min()!)
            context.fillEllipse(in:rect.insetBy(dx:-box.width*0.035,dy:-box.height*0.025))
        }
    }
    guard let image=context.makeImage() else {throw PortraitFailure.image}
    return CIImage(cgImage:image).applyingFilter("CIGaussianBlur",parameters:[kCIInputRadiusKey:Double(min(width,height))*0.012]).cropped(to:canvas)
}

do {
    guard CommandLine.arguments.count==5,CommandLine.arguments[1]=="--request",CommandLine.arguments[3]=="--response" else {throw PortraitFailure.invalidArguments}
    let decoder=JSONDecoder();decoder.keyDecodingStrategy = .convertFromSnakeCase
    let input=try decoder.decode(Input.self,from:Data(contentsOf:URL(fileURLWithPath:CommandLine.arguments[2])))
    guard input.strength.isFinite,input.strength>=0,input.strength<=1,input.skinExposure.isFinite,abs(input.skinExposure)<=0.5,input.sourcePath != input.outputPath else {throw PortraitFailure.invalidArguments}
    let asset=AVURLAsset(url:URL(fileURLWithPath:input.sourcePath))
    guard let track=asset.tracks(withMediaType:.video).first else {throw PortraitFailure.invalidVideo}
    let width=Int(track.naturalSize.width),height=Int(track.naturalSize.height)
    guard width>0,height>0,width<=1920,height<=1920 else {throw PortraitFailure.invalidVideo}
    let reader=try AVAssetReader(asset:asset)
    let readerOutput=AVAssetReaderTrackOutput(track:track,outputSettings:[kCVPixelBufferPixelFormatTypeKey as String:kCVPixelFormatType_32BGRA])
    readerOutput.alwaysCopiesSampleData=false;reader.add(readerOutput)
    let writer=try AVAssetWriter(outputURL:URL(fileURLWithPath:input.outputPath),fileType:.mp4)
    let writerInput=AVAssetWriterInput(mediaType:.video,outputSettings:[AVVideoCodecKey:AVVideoCodecType.h264,
        AVVideoWidthKey:width,AVVideoHeightKey:height,AVVideoCompressionPropertiesKey:[AVVideoAverageBitRateKey:6_000_000]])
    writerInput.expectsMediaDataInRealTime=false
    let adaptor=AVAssetWriterInputPixelBufferAdaptor(assetWriterInput:writerInput,sourcePixelBufferAttributes:[
        kCVPixelBufferPixelFormatTypeKey as String:kCVPixelFormatType_32BGRA,kCVPixelBufferWidthKey as String:width,
        kCVPixelBufferHeightKey as String:height,kCVPixelBufferIOSurfacePropertiesKey as String:[:] ])
    writer.add(writerInput)
    guard reader.startReading(),writer.startWriting() else {throw PortraitFailure.reader}
    writer.startSession(atSourceTime:.zero)
    let context=CIContext(options:[.cacheIntermediates:false])
    let colorSpace=CGColorSpaceCreateDeviceRGB()
    var count=0,faceCount=0
    var lastTime=CMTime.zero
    while let sample=readerOutput.copyNextSampleBuffer() {
        try autoreleasepool {
            guard let pixel=CMSampleBufferGetImageBuffer(sample) else {throw PortraitFailure.image}
            let original=CIImage(cvPixelBuffer:pixel)
            let request=VNDetectFaceLandmarksRequest()
            request.revision=VNDetectFaceLandmarksRequestRevision3
            try VNImageRequestHandler(cvPixelBuffer:pixel,orientation:.up,options:[:]).perform([request])
            let faces=(request.results ?? []).filter{$0.confidence>=0.7 && $0.landmarks?.outerLips != nil && $0.landmarks?.leftEye != nil && $0.landmarks?.rightEye != nil}
            var result=original
            if !faces.isEmpty {
                faceCount+=1
                let mask=try faceMask(faces,width:width,height:height)
                let smooth=original.applyingFilter("CINoiseReduction",parameters:["inputNoiseLevel":0.02+input.strength*0.06,"inputSharpness":0.5])
                let skin=original.applyingFilter("CIBlendWithMask",parameters:[kCIInputBackgroundImageKey:smooth,kCIInputMaskImageKey:CIImage(color:CIColor(red:1-input.strength,green:1-input.strength,blue:1-input.strength)).cropped(to:original.extent)]).applyingFilter("CIExposureAdjust",parameters:[kCIInputEVKey:input.skinExposure])
                result=skin.applyingFilter("CIBlendWithMask",parameters:[kCIInputBackgroundImageKey:original,kCIInputMaskImageKey:mask])
            }
            guard let pool=adaptor.pixelBufferPool else {throw PortraitFailure.writer}
            var destination:CVPixelBuffer?
            guard CVPixelBufferPoolCreatePixelBuffer(nil,pool,&destination)==kCVReturnSuccess,let destination=destination else {throw PortraitFailure.writer}
            context.render(result,to:destination,bounds:original.extent,colorSpace:colorSpace)
            while !writerInput.isReadyForMoreMediaData {
                if writer.status == .failed {throw PortraitFailure.writer}
                Thread.sleep(forTimeInterval:0.002)
            }
            lastTime=CMSampleBufferGetPresentationTimeStamp(sample)
            guard adaptor.append(destination,withPresentationTime:lastTime) else {throw PortraitFailure.writer}
            count+=1
        }
    }
    guard reader.status == .completed else {throw PortraitFailure.reader}
    writerInput.markAsFinished()
    let finished=DispatchSemaphore(value:0)
    writer.finishWriting{finished.signal()};finished.wait()
    guard writer.status == .completed,count>0 else {throw PortraitFailure.writer}
    guard faceCount>0 else {throw PortraitFailure.noFace}
    let encoder=JSONEncoder();encoder.keyEncodingStrategy = .convertToSnakeCase
    try encoder.encode(Output(frameCount:count,faceFrameCount:faceCount,durationSeconds:CMTimeGetSeconds(asset.duration))).write(to:URL(fileURLWithPath:CommandLine.arguments[4]),options:.atomic)
} catch {
    var reason="processing_failed"
    if let failure=error as? PortraitFailure {
        switch failure {case .noFace:reason="no_face";default:break}
    }
    if CommandLine.arguments.count==5,CommandLine.arguments[3]=="--response",
       let data=try? JSONSerialization.data(withJSONObject:["status":"failed","reason":reason]) {
        try? data.write(to:URL(fileURLWithPath:CommandLine.arguments[4]),options:.atomic)
    }
    FileHandle.standardError.write(Data("本地人像处理失败：\(error)\n".utf8));exit(1)
}
