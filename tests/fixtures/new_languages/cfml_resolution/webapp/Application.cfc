component {

	this.name = "webapp";
	this.root = getDirectoryFromPath( getCurrentTemplatePath() );
	this.libPath = expandPath( "../shared/lib" );

	this.mappings[ "/lib" ] = this.libPath;
	this.mappings[ '/core' ] = this.root & "core";
	this.mappings[ "/abs" ] = "C:/somewhere/else";
	this.mappings[ "/dyn" ] = computePath();

}
