require 'formula'

class Howdoi < Formula
  homepage 'https://github.com/gleitz/howdoi/'
  url 'http://pypi.python.org/packages/source/h/howdoi/howdoi-1.1.3.tar.gz'
  sha1 '35e6bbfbd444d2edddab61bba5e9ec95d642ddcf'

  def install
    setup_args = ['setup.py', 'install']
    system "python", *setup_args
  end

  def scripts_folder
    HOMEBREW_PREFIX/"share/python"
  end

  def caveats
    <<-EOS.undent
      To run the `howdoi` command, you'll need to add Python's script directory to your PATH:
        #{scripts_folder}
    EOS
  end
end
