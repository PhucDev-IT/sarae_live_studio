package co.sarae.camera.engine

import android.opengl.EGL14 as E
import android.opengl.EGLExt
import android.opengl.GLES20 as G
import android.opengl.GLES11Ext
import android.graphics.SurfaceTexture
import android.view.Surface
import android.os.SystemClock
import android.os.Trace
import co.sarae.camera.bridge.RenderConfiguration
import java.nio.ByteBuffer
import java.nio.ByteOrder
import android.graphics.Bitmap
import android.graphics.Matrix
import java.io.File

/** All operations, including SurfaceTexture.updateTexImage, belong to the renderer thread. */
internal class EglOutput(output: Surface) {
    private val display = E.eglGetDisplay(E.EGL_DEFAULT_DISPLAY)
    private val context: android.opengl.EGLContext
    private val window: android.opengl.EGLSurface
    private val program: Int
    val width: Int
    val height: Int
    private val vertices = ByteBuffer.allocateDirect(16 * 4).order(ByteOrder.nativeOrder()).asFloatBuffer().apply {
        put(floatArrayOf(-1f,-1f,0f,0f, 1f,-1f,1f,0f, -1f,1f,0f,1f, 1f,1f,1f,1f)); position(0)
    }
    init {
        check(E.eglInitialize(display, IntArray(2), 0, IntArray(2), 0)) { "eglInitialize" }
        val configs = arrayOfNulls<android.opengl.EGLConfig>(1)
        val count = IntArray(1)
        check(E.eglChooseConfig(display, intArrayOf(E.EGL_RED_SIZE,8,E.EGL_GREEN_SIZE,8,E.EGL_BLUE_SIZE,8,
            E.EGL_ALPHA_SIZE,8,E.EGL_RENDERABLE_TYPE,E.EGL_OPENGL_ES2_BIT,E.EGL_SURFACE_TYPE,E.EGL_WINDOW_BIT,E.EGL_NONE),
            0, configs,0,1,count,0) && count[0] > 0)
        context = E.eglCreateContext(display,configs[0],E.EGL_NO_CONTEXT,intArrayOf(E.EGL_CONTEXT_CLIENT_VERSION,2,E.EGL_NONE),0)
        check(context != E.EGL_NO_CONTEXT)
        window = E.eglCreateWindowSurface(display, configs[0],output,intArrayOf(E.EGL_NONE),0)
        check(window != E.EGL_NO_SURFACE)
        check(E.eglMakeCurrent(display,window,window,context))
        val extent=IntArray(1)
        check(E.eglQuerySurface(display,window,E.EGL_WIDTH,extent,0)); width=extent[0]
        check(E.eglQuerySurface(display,window,E.EGL_HEIGHT,extent,0)); height=extent[0]
        require(width>0 && height>0) { "Invalid consumer EGL extent ${width}x$height" }
        E.eglSwapInterval(display,0)
        val vs = shader(G.GL_VERTEX_SHADER, "attribute vec4 p; attribute vec2 uv; uniform mat4 m; varying vec2 v; void main(){gl_Position=p; v=(m*vec4(uv,0.,1.)).xy;}")
        val fs = shader(G.GL_FRAGMENT_SHADER, "#extension GL_OES_EGL_image_external : require\nprecision mediump float; uniform samplerExternalOES tex; varying vec2 v; void main(){gl_FragColor=texture2D(tex,v);}")
        program = G.glCreateProgram()
        G.glAttachShader(program,vs); G.glAttachShader(program,fs); G.glLinkProgram(program)
        val linked=IntArray(1); G.glGetProgramiv(program,G.GL_LINK_STATUS,linked,0)
        check(linked[0] != 0) { G.glGetProgramInfoLog(program) }
        G.glDeleteShader(vs); G.glDeleteShader(fs)
    }
    private fun shader(type: Int, source: String): Int {
        val id=G.glCreateShader(type); G.glShaderSource(id,source); G.glCompileShader(id)
        val ok=IntArray(1); G.glGetShaderiv(id,G.GL_COMPILE_STATUS,ok,0)
        check(ok[0] != 0) { G.glGetShaderInfoLog(id) }; return id
    }
    fun createInput(): Pair<Int,SurfaceTexture> {
        val names=IntArray(1); G.glGenTextures(1,names,0)
        G.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES,names[0])
        G.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES,G.GL_TEXTURE_MIN_FILTER,G.GL_LINEAR)
        G.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES,G.GL_TEXTURE_MAG_FILTER,G.GL_LINEAR)
        G.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES,G.GL_TEXTURE_WRAP_S,G.GL_CLAMP_TO_EDGE)
        G.glTexParameteri(GLES11Ext.GL_TEXTURE_EXTERNAL_OES,G.GL_TEXTURE_WRAP_T,G.GL_CLAMP_TO_EDGE)
        return names[0] to SurfaceTexture(names[0]).apply { setDefaultBufferSize(640,480) }
    }
    fun setGeometry(videoWidth: Int, videoHeight: Int, mediaRotation: Int, config: RenderConfiguration) {
        val points=RenderGeometry.positions(videoWidth,videoHeight,mediaRotation,width,height,config)
        for(i in 0..3) { vertices.put(i*4,points[i*2]); vertices.put(i*4+1,points[i*2+1]) }
    }
    data class DrawTiming(val drawNs: Long, val swapNs: Long)
    fun draw(texture: Int, matrix: FloatArray, ptsNs: Long, captureFile: File? = null): DrawTiming {
        val begin=SystemClock.elapsedRealtimeNanos()
        Trace.beginSection("Sarae.draw")
        G.glViewport(0,0,width,height)
        G.glClearColor(0f,0f,0f,1f); G.glClear(G.GL_COLOR_BUFFER_BIT)
        G.glUseProgram(program)
        G.glActiveTexture(G.GL_TEXTURE0); G.glBindTexture(GLES11Ext.GL_TEXTURE_EXTERNAL_OES,texture)
        G.glUniform1i(G.glGetUniformLocation(program,"tex"),0)
        G.glUniformMatrix4fv(G.glGetUniformLocation(program,"m"),1,false,matrix,0)
        val position=G.glGetAttribLocation(program,"p"); val uv=G.glGetAttribLocation(program,"uv")
        vertices.position(0); G.glVertexAttribPointer(position,2,G.GL_FLOAT,false,16,vertices); G.glEnableVertexAttribArray(position)
        vertices.position(2); G.glVertexAttribPointer(uv,2,G.GL_FLOAT,false,16,vertices); G.glEnableVertexAttribArray(uv)
        G.glDrawArrays(G.GL_TRIANGLE_STRIP,0,4)
        check(G.glGetError() == G.GL_NO_ERROR) { "GL draw error" }
        if(captureFile != null) {
            // Calibration only: never enabled in measured cadence runs.
            val pixels=ByteBuffer.allocateDirect(width*height*4).order(ByteOrder.nativeOrder())
            G.glReadPixels(0,0,width,height,G.GL_RGBA,G.GL_UNSIGNED_BYTE,pixels)
            check(G.glGetError()==G.GL_NO_ERROR)
            val bitmap=Bitmap.createBitmap(width,height,Bitmap.Config.ARGB_8888)
            pixels.rewind(); bitmap.copyPixelsFromBuffer(pixels)
            val upright=Bitmap.createBitmap(bitmap,0,0,width,height,Matrix().apply { setScale(1f,-1f) },false)
            captureFile.parentFile?.mkdirs()
            captureFile.outputStream().use { check(upright.compress(Bitmap.CompressFormat.PNG,100,it)) }
            if(upright !== bitmap) upright.recycle()
            bitmap.recycle()
        }
        check(EGLExt.eglPresentationTimeANDROID(display,window,ptsNs))
        Trace.endSection()
        val beforeSwap=SystemClock.elapsedRealtimeNanos()
        Trace.beginSection("Sarae.swap")
        check(E.eglSwapBuffers(display,window)) { "eglSwapBuffers error=${E.eglGetError()}" }
        Trace.endSection()
        return DrawTiming(beforeSwap-begin,SystemClock.elapsedRealtimeNanos()-beforeSwap)
    }
    fun releaseInput(id: Int, texture: SurfaceTexture) { texture.release(); G.glDeleteTextures(1,intArrayOf(id),0) }
    fun close() {
        G.glDeleteProgram(program)
        E.eglMakeCurrent(display,E.EGL_NO_SURFACE,E.EGL_NO_SURFACE,E.EGL_NO_CONTEXT)
        E.eglDestroySurface(display,window); E.eglDestroyContext(display,context)
        E.eglReleaseThread(); E.eglTerminate(display)
    }
}
