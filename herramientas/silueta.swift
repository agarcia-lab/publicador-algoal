// Tu silueta, fotograma a fotograma (Vision, de macOS): para poner texto DETRÁS de ti.
// Uso: swift silueta.swift video.mp4 | ffmpeg -f rawvideo -pix_fmt gray -s 1080x1920 -r 30 -i - mascara.mp4
// Escribe en la salida estándar cada fotograma como una máscara gris de 1080x1920 (blanco = tú). Solo macOS.
import AVFoundation
import CoreImage
import Foundation
import Vision

let ruta = CommandLine.arguments[1]
let calidad: VNGeneratePersonSegmentationRequest.QualityLevel =
    CommandLine.arguments.count > 2 && CommandLine.arguments[2] == "rapida" ? .balanced : .accurate
let ancho = 1080, alto = 1920

let activo = AVURLAsset(url: URL(fileURLWithPath: ruta))
let pista = activo.tracks(withMediaType: .video)[0]
let lector = try! AVAssetReader(asset: activo)
let salida = AVAssetReaderTrackOutput(track: pista, outputSettings: [
    kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
lector.add(salida)
lector.startReading()

let contexto = CIContext(options: [.useSoftwareRenderer: false])
let pedido = VNGeneratePersonSegmentationRequest()
pedido.qualityLevel = calidad
pedido.outputPixelFormat = kCVPixelFormatType_OneComponent8
var bytes = [UInt8](repeating: 0, count: ancho * alto)
let gris = CGColorSpaceCreateDeviceGray()
var n = 0

while let muestra = salida.copyNextSampleBuffer(), let imagen = CMSampleBufferGetImageBuffer(muestra) {
    try? VNImageRequestHandler(cvPixelBuffer: imagen).perform([pedido])
    if let mascara = pedido.results?.first?.pixelBuffer {
        let ci = CIImage(cvPixelBuffer: mascara)
        let escalada = ci.transformed(by: CGAffineTransform(scaleX: CGFloat(ancho) / ci.extent.width,
                                                            y: CGFloat(alto) / ci.extent.height))
        contexto.render(escalada, toBitmap: &bytes, rowBytes: ancho, bounds: CGRect(x: 0, y: 0, width: ancho, height: alto),
                        format: .L8, colorSpace: gris)
    } else {
        bytes = [UInt8](repeating: 0, count: ancho * alto)
    }
    FileHandle.standardOutput.write(Data(bytes))
    n += 1
}
FileHandle.standardError.write("silueta: \(n) fotogramas\n".data(using: .utf8)!)
